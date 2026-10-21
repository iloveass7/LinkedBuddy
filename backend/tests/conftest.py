import asyncio
import re
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.db.engine import get_session, make_engine, make_sessionmaker
from linkedbuddy.db.models import Job
from linkedbuddy.main import create_app
from linkedbuddy.settings.service import clear_settings_cache

BACKEND_DIR = Path(__file__).resolve().parents[1]
OWNER_PASSWORD = "correct horse battery staple"  # noqa: S105 - test value
# Minimal argon2 cost so login tests stay fast; verify() reads the cost from the hash itself.
_TEST_HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(OWNER_PASSWORD)


def _supabase_ref(url: URL) -> str | None:
    """Supabase project ref: pooler user `postgres.<ref>` or direct host `db.<ref>.supabase.co`."""
    if url.username and (m := re.fullmatch(r"postgres\.([a-z0-9]+)", url.username)):
        return m.group(1)
    if url.host and (m := re.fullmatch(r"db\.([a-z0-9]+)\.supabase\.co", url.host)):
        return m.group(1)
    return None


def check_test_db_url(test_url: str | None, real_url: str | None) -> None:
    """Refuse to run unless the tests target a separate, session-pooler test database.

    Messages never include the URLs: they carry the database password.
    """
    if not test_url:
        raise RuntimeError("TEST_DATABASE_URL is not set; the DB tests need a separate test DB")
    test = make_url(test_url)
    if test.port == 6543:
        raise RuntimeError("TEST_DATABASE_URL uses port 6543 (transaction pooler); use 5432")
    if not real_url:
        return
    real = make_url(real_url)
    test_ref, real_ref = _supabase_ref(test), _supabase_ref(real)
    if test_ref or real_ref:
        # Poolers share a host across projects and the direct host reaches the same project, so
        # when either URL names a Supabase project only the refs decide. A URL without a ref
        # (e.g. a placeholder on the shared pooler host) cannot be that project.
        same = test_ref == real_ref
    else:
        same = (test.host, test.port, test.database) == (real.host, real.port, real.database)
    if same:
        raise RuntimeError("TEST_DATABASE_URL points at DATABASE_URL; tests wipe the database")


def pytest_sessionstart(session: pytest.Session) -> None:
    try:
        settings = Settings()  # type: ignore[call-arg]
        check_test_db_url(settings.test_database_url, settings.database_url)
    except Exception as exc:  # also covers missing required settings
        pytest.exit(f"Refusing to run: {type(exc).__name__}: {exc}", returncode=2)


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["url"] = url
    return cfg


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    """The settings cache is per process; tests roll their writes back, so never share it."""
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = Settings().test_database_url  # type: ignore[call-arg]
    assert url
    return url


@pytest.fixture(scope="session")
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    # Alembic's env.py runs its own event loop, so run it off this one.
    await asyncio.to_thread(command.upgrade, alembic_config(test_database_url), "head")
    eng = make_engine(test_database_url)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session in an outer transaction that is rolled back; commit() only releases a savepoint."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


async def _wipe_jobs(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as s:
        await s.execute(delete(Job))
        await s.commit()


@pytest.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions that really commit, for the job queue (SKIP LOCKED needs concurrent transactions).

    Rows are visible to every test, so the jobs table is wiped before and after.
    """
    maker = make_sessionmaker(engine)
    await _wipe_jobs(maker)
    yield maker
    await _wipe_jobs(maker)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="dev",
        database_url="postgresql+asyncpg://user:pw@localhost:5432/test",
        app_encryption_key="fake-encryption-key",  # type: ignore[arg-type]
        owner_password_hash=_TEST_HASH,  # type: ignore[arg-type]
    )


@pytest.fixture
def client_for(db: AsyncSession) -> Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]:
    """Factory for a client over an app built from custom settings, using the test session."""

    @asynccontextmanager
    async def _make(settings: Settings) -> AsyncIterator[AsyncClient]:
        app = create_app(settings)

        async def _session() -> AsyncIterator[AsyncSession]:
            yield db

        app.dependency_overrides[get_session] = _session
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    return _make


@pytest.fixture
async def client(
    settings: Settings, client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]
) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests use the rolled-back test session."""
    async with client_for(settings) as c:
        yield c


@pytest.fixture
async def plain_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client with no database; for routes that never touch it."""
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def count_queries(engine: AsyncEngine) -> Iterator[list[str]]:
    """Every SQL statement the engine runs while the test body executes, in order.

    Includes the auth session lookup and savepoint housekeeping; filter those out in the test.
    """
    statements: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    yield statements
    event.remove(engine.sync_engine, "before_cursor_execute", _record)
