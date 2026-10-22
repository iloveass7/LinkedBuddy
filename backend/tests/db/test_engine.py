import logging
import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from linkedbuddy.config import Settings
from linkedbuddy.db.engine import (
    API_DB_CONNECTIONS,
    SCHEDULER_DB_CONNECTIONS,
    SUPABASE_SESSION_POOL_SIZE,
    engine_for,
    worker_db_connections,
)


async def test_db_errors_hide_parameters(engine: AsyncEngine) -> None:
    """jobs.error and logs carry str(exc); bound values (README text, post bodies) must not leak."""
    assert engine.sync_engine.hide_parameters
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError) as caught:
            await conn.execute(text("SELECT :v::text, 1/0"), {"v": "secret-readme-text"})
    assert "secret-readme-text" not in str(caught.value)


SLOW_SQL = "SELECT pg_sleep(:secs), CAST(:marker AS text) AS an_alias_making_it_long_" + "x" * 40


async def _run_slow_statement(eng: AsyncEngine) -> None:
    try:
        async with eng.connect() as conn:
            await conn.execute(text(SLOW_SQL), {"secs": 0.15, "marker": "secret-param"})
    finally:
        await eng.dispose()


def _slow_logs(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "linkedbuddy.db.engine"]


async def test_slow_statement_logged_in_dev_without_parameters(
    settings: Settings, test_database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    dev = settings.model_copy(update={"database_url": test_database_url, "slow_query_ms": 100})
    with caplog.at_level(logging.WARNING, logger="linkedbuddy.db.engine"):
        await _run_slow_statement(engine_for(dev, max_connections=1))
    [message] = [m for m in _slow_logs(caplog) if "pg_sleep" in m]
    assert re.fullmatch(r"slow query \d+ ms: SELECT pg_sleep\(.{60,}", message)
    assert len(message.split(": ", 1)[1]) == 80  # the statement is cut to 80 characters
    assert "secret-param" not in message and "0.15" not in message


async def test_slow_statement_not_logged_outside_dev(
    settings: Settings, test_database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    prod = settings.model_copy(update={"database_url": test_database_url, "app_env": "prod"})
    with caplog.at_level(logging.WARNING, logger="linkedbuddy.db.engine"):
        await _run_slow_statement(engine_for(prod, max_connections=1))
    assert _slow_logs(caplog) == []


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u:p@db.example.com:5432/postgres",
        app_encryption_key="k",
        owner_password_hash="h",
    )


async def test_engine_for_caps_connections_at_max_connections() -> None:
    eng = engine_for(_settings(), max_connections=3)
    try:
        pool = eng.sync_engine.pool
        assert pool.size() == 3
        assert pool._max_overflow == 0
    finally:
        await eng.dispose()


def test_dev_process_layout_fits_supabase_session_pool() -> None:
    # scripts/dev.sh runs the API, a worker (concurrency 4), a video worker (concurrency 1) and
    # the scheduler. Each worker also holds one LISTEN connection outside its pool.
    total = (
        API_DB_CONNECTIONS
        + (worker_db_connections(4) + 1)
        + (worker_db_connections(1) + 1)
        + SCHEDULER_DB_CONNECTIONS
    )
    assert total <= SUPABASE_SESSION_POOL_SIZE
