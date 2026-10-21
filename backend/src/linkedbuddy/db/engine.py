"""Async engine and session helpers (DATABASE.md section 2)."""

import logging
import time
from collections.abc import AsyncIterator
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


def reject_transaction_pooler(url: str) -> None:
    if make_url(url).port == TRANSACTION_POOLER_PORT:
        raise ValueError("Port 6543 is the transaction pooler; use the session pooler on 5432")


def make_engine(url: str, *, slow_query_ms: int | None = None) -> AsyncEngine:
    """Build the process-wide engine. Refuses the Supabase transaction pooler (port 6543).

    With `slow_query_ms`, statements slower than that are logged (see `log_slow_queries`).
    """
    reject_transaction_pooler(url)
    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        # str(DBAPIError) would otherwise embed bound values (README text, post bodies) in
        # logs and jobs.error (SECURITY.md 7).
        hide_parameters=True,
    )
    if slow_query_ms is not None:
        log_slow_queries(engine, slow_query_ms)
    return engine


def engine_for(settings: Settings) -> AsyncEngine:
    """The engine for DATABASE_URL; in dev it logs slow statements (DATABASE.md 7.7)."""
    slow = settings.slow_query_ms if settings.app_env == "dev" else None
    return make_engine(settings.database_url, slow_query_ms=slow)


def log_slow_queries(engine: AsyncEngine, threshold_ms: int) -> None:
    """Warn about statements over `threshold_ms`: duration and the first 80 characters of the SQL.

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
        if elapsed_ms > threshold_ms:
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
