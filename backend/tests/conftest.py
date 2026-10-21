from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from linkedbuddy.config import Settings
from linkedbuddy.main import create_app


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
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
