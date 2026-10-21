"""Async engine and session helpers (DATABASE.md section 2)."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TRANSACTION_POOLER_PORT = 6543


def reject_transaction_pooler(url: str) -> None:
    if make_url(url).port == TRANSACTION_POOLER_PORT:
        raise ValueError("Port 6543 is the transaction pooler; use the session pooler on 5432")


def make_engine(url: str) -> AsyncEngine:
    """Build the process-wide engine. Refuses the Supabase transaction pooler (port 6543)."""
    reject_transaction_pooler(url)
    return create_async_engine(
        url,
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, from the app-wide sessionmaker."""
    async with request.app.state.sessionmaker() as session:
        yield session
