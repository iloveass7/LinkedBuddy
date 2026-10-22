"""Async engine and session helpers (DATABASE.md section 2)."""

import logging
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from linkedbuddy.config import Settings

log = logging.getLogger(__name__)

TRANSACTION_POOLER_PORT = 6543
SLOW_QUERY_TEXT_CHARS = 80

# Supabase's session pooler serves at most this many client connections per project (free tier
# default), across every process. Going over fails the connect with EMAXCONNSESSION, so each
# process gets a fixed share and waits in its own pool instead (DATABASE.md 2).
SUPABASE_SESSION_POOL_SIZE = 15
API_DB_CONNECTIONS = 4
SCHEDULER_DB_CONNECTIONS = 1


def worker_db_connections(concurrency: int) -> int:
    """One connection per job slot, plus one for the claim loop and job outcomes."""
    return concurrency + 1


def reject_transaction_pooler(url: str) -> None:
    if make_url(url).port == TRANSACTION_POOLER_PORT:
        raise ValueError("Port 6543 is the transaction pooler; use the session pooler on 5432")


def make_engine(
    url: str, *, max_connections: int = 10, slow_query_ms: int | None = None
) -> AsyncEngine:
    """Build the process-wide engine. Refuses the Supabase transaction pooler (port 6543).

    The pool never opens more than `max_connections`; extra checkouts wait (up to 30 s).

    Statement time always counts towards the current request's `DbTime` (Server-Timing). With
    `slow_query_ms`, statements slower than that are also logged (see `_time_statements`).
    """
    reject_transaction_pooler(url)
    engine = create_async_engine(
        url,
        # Idle connections stay open: a fresh TLS connect to a remote Supabase costs far more
        # than the query. No overflow, so the cap is hard.
        pool_size=max_connections,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=1800,
        # str(DBAPIError) would otherwise embed bound values (README text, post bodies) in
        # logs and jobs.error (SECURITY.md 7).
        hide_parameters=True,
    )
    _time_statements(engine, slow_query_ms)
    return engine


def engine_for(settings: Settings, *, max_connections: int) -> AsyncEngine:
    """The engine for DATABASE_URL; in dev it logs slow statements (DATABASE.md 7.7)."""
    slow = settings.slow_query_ms if settings.app_env == "dev" else None
    return make_engine(settings.database_url, max_connections=max_connections, slow_query_ms=slow)


class DbTime:
    """Milliseconds of DB statements run while this object is `current_db_time`."""

    __slots__ = ("ms",)

    def __init__(self) -> None:
        self.ms = 0.0


# Set per request by the timing middleware. The hooks add to the object rather than set the
# variable, so time spent in SQLAlchemy's greenlets (which copy the context) is not lost.
current_db_time: ContextVar[DbTime | None] = ContextVar("current_db_time", default=None)


def _time_statements(engine: AsyncEngine, slow_query_ms: int | None) -> None:
    """Add each statement's time to `current_db_time`; with `slow_query_ms`, warn about slower
    statements with their duration and the first 80 characters of the SQL.

    Never the parameters (they carry README text, post bodies and tokens). The time includes the
    network round trip, which alone is ~100-400 ms to a remote Supabase; raise SLOW_QUERY_MS then.
    """

    def start(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> None:
        if context is not None:  # None only for some dialect-internal statements
            context._lb_started = time.perf_counter()

    def end(conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool) -> None:
        started = getattr(context, "_lb_started", None)
        if started is None:
            return
        elapsed_ms = (time.perf_counter() - started) * 1000
        if (tracker := current_db_time.get()) is not None:
            tracker.ms += elapsed_ms
        if slow_query_ms is not None and elapsed_ms > slow_query_ms:
            text = " ".join(statement.split())[:SLOW_QUERY_TEXT_CHARS]
            log.warning("slow query %.0f ms: %s", elapsed_ms, text)

    event.listen(engine.sync_engine, "before_cursor_execute", start)
    event.listen(engine.sync_engine, "after_cursor_execute", end)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, from the app-wide sessionmaker."""
    async with request.app.state.sessionmaker() as session:
        yield session
