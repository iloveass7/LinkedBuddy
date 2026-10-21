import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from linkedbuddy.config import Settings
from linkedbuddy.db.engine import get_session, make_engine
from linkedbuddy.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]


def check_test_db_url(test_url: str | None, real_url: str | None) -> None:
    """Refuse to run unless the tests target a separate, session-pooler test database.

    Messages never include the URLs: they carry the database password.
    """
    if not test_url:
        raise RuntimeError("TEST_DATABASE_URL is not set; the DB tests need a separate test DB")
    test = make_url(test_url)
    if test.port == 6543:
        raise RuntimeError("TEST_DATABASE_URL uses port 6543 (transaction pooler); use 5432")
    if real_url:
        real = make_url(real_url)
        # The username matters: Supabase poolers share a host and encode the project in it.
        keys = ("host", "port", "database", "username")
        if all(getattr(test, k) == getattr(real, k) for k in keys):
            raise RuntimeError("TEST_DATABASE_URL equals DATABASE_URL; tests wipe the database")


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


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="dev",
        database_url="postgresql+asyncpg://user:pw@localhost:5432/test",
        app_encryption_key="fake-encryption-key",  # type: ignore[arg-type]
        owner_password_hash="fake-hash",  # type: ignore[arg-type]
    )


@pytest.fixture
async def client(settings: Settings, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTP client whose requests use the rolled-back test session."""
    app = create_app(settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def plain_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client with no database; for routes that never touch it."""
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
