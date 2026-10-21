import pytest
from pydantic import ValidationError

from linkedbuddy.config import Settings


def _settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://user:pw@localhost:5432/test",
        app_encryption_key="k",  # type: ignore[arg-type]
        owner_password_hash="h",  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "http://*",
        "null",
        "localhost:5173",
        "ftp://example.com",
        "https://example.com/",
        "https://example.com/app",
        "https://example.com?x=1",
        "https://user@example.com",
        "https://",
        "",
    ],
)
def test_frontend_origins_rejects_unsafe(origin: str) -> None:
    with pytest.raises(ValidationError):
        _settings(frontend_origins=["https://ok.example", origin])


def test_frontend_origins_accepts_scheme_host_port() -> None:
    origins = ["http://localhost:5173", "https://app.example.com", "http://127.0.0.1:8080"]
    assert _settings(frontend_origins=origins).frontend_origins == origins
