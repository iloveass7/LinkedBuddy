"""FastAPI app factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings, get_settings
from linkedbuddy.db.engine import get_session, make_engine, make_sessionmaker
from linkedbuddy.http import make_http_client
from linkedbuddy.llm.gemini import make_llm


class HealthResponse(BaseModel):
    status: str
    db: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.engine = make_engine(app.state.settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.http = make_http_client()
    app.state.llm = make_llm(app.state.settings)
    try:
        yield
    finally:
        await app.state.http.aclose()
        await app.state.engine.dispose()


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
    async def health(
        response: Response, session: Annotated[AsyncSession, Depends(get_session)]
    ) -> HealthResponse:
        try:
            await session.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", db="error")
        return HealthResponse(status="ok", db="ok")

    return app


def __getattr__(name: str) -> FastAPI:
    # Lazy `app` so `uvicorn linkedbuddy.main:app` works without building settings at import time.
    if name == "app":
        return create_app()
    raise AttributeError(name)
