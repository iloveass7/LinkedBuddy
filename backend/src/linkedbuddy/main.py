"""FastAPI app factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel

from linkedbuddy.config import Settings, get_settings


class HealthResponse(BaseModel):
    status: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Shared resources (DB engine, httpx client, Gemini client) are created here in later tasks.
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    dev = settings.app_env == "dev"
    app = FastAPI(
        title="LinkedBuddy",
        lifespan=_lifespan,
        docs_url="/docs" if dev else None,
        redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )
    app.state.settings = settings

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


def __getattr__(name: str) -> FastAPI:
    # Lazy `app` so `uvicorn linkedbuddy.main:app` works without building settings at import time.
    if name == "app":
        return create_app()
    raise AttributeError(name)
