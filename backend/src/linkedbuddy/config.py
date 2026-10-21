"""Application settings. The only place environment variables are read."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    # Later files override earlier ones: backend/.env wins over the repo-root .env.
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "prod"] = "dev"
    database_url: str
    test_database_url: str | None = None
    app_encryption_key: SecretStr
    owner_password_hash: SecretStr
    github_token: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_model_fast: str = "gemini-flash-lite-latest"
    gemini_model_quality: str = "gemini-flash-latest"
    gemini_rpm_fast: int = 10
    gemini_rpm_quality: int = 5
    frontend_origins: list[str] = ["http://localhost:5173"]
    media_root: Path = Path("var/media")
    poll_interval_minutes: int = 180
    brag_command: list[str] | None = None
    video_timeout_s: int = 600
    allow_public_bind: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
