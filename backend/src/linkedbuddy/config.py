"""Application settings. The only place environment variables are read."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent

# scheme://host[:port] only: no wildcard, userinfo, path, query or fragment.
_ORIGIN_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:\d{1,5})?")


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
    media_root: Path = Path("var/media")  # relative paths are resolved against the backend dir
    # Relative to the backend dir; falls back to `mmdc` on PATH when this file does not exist.
    mmdc_path: str = "tools/node_modules/.bin/mmdc"
    # Optional Puppeteer config (browser executablePath); defaults to tools/puppeteer-config.json
    # when that file exists. Never put `--no-sandbox` in it (SECURITY.md 5.5).
    mmdc_puppeteer_config: Path | None = None
    poll_interval_minutes: int = 180
    brag_command: list[str] | None = None
    video_timeout_s: int = 600
    allow_public_bind: bool = False

    @property
    def media_dir(self) -> Path:
        """MEDIA_ROOT as an absolute path."""
        return _BACKEND_DIR / self.media_root  # an absolute media_root wins over the prefix

    @property
    def mmdc_executable(self) -> str:
        local = _BACKEND_DIR / self.mmdc_path
        return str(local) if local.is_file() else "mmdc"

    @property
    def mmdc_puppeteer_file(self) -> Path | None:
        if self.mmdc_puppeteer_config is not None:
            return _BACKEND_DIR / self.mmdc_puppeteer_config
        default = _BACKEND_DIR / "tools" / "puppeteer-config.json"
        return default if default.is_file() else None

    @field_validator("frontend_origins")
    @classmethod
    def _origins_are_exact(cls, origins: list[str]) -> list[str]:
        # With allow_credentials=True a "*" would make CORS echo any Origin (SECURITY.md 3.2).
        for origin in origins:
            if not _ORIGIN_RE.fullmatch(origin):
                raise ValueError("frontend_origins must be http(s)://host[:port] with no path")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
