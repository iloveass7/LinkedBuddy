"""FastAPI app factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.auth import router as auth_router
from linkedbuddy.config import Settings, get_settings
from linkedbuddy.db.engine import get_session, make_engine, make_sessionmaker
from linkedbuddy.http import make_http_client
from linkedbuddy.jobs import router as jobs_router
from linkedbuddy.llm.gemini import make_llm
from linkedbuddy.repos import router as repos_router
from linkedbuddy.security.auth import LoginRateLimiter, require_owner
from linkedbuddy.security.middleware import SecurityMiddleware


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
    app.state.login_limiter = LoginRateLimiter()

    # Added last = outermost: CORS answers preflights, then the CSRF check and headers wrap the app.
    app.add_middleware(SecurityMiddleware, hsts=not dev)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Requested-With"],
    )

    public = APIRouter(prefix="/api")  # health and login only (see PUBLIC_ROUTES)
    api = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])  # every other router
    public.include_router(auth_router.public_router)
    api.include_router(auth_router.router)
    api.include_router(jobs_router.router)
    api.include_router(repos_router.router)

    @public.get("/health", response_model=HealthResponse)
    async def health(
        response: Response, session: Annotated[AsyncSession, Depends(get_session)]
    ) -> HealthResponse:
        try:
            await session.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthResponse(status="degraded", db="error")
        return HealthResponse(status="ok", db="ok")

    app.include_router(public)
    app.include_router(api)
    return app


def __getattr__(name: str) -> FastAPI:
    # Lazy `app` so `uvicorn linkedbuddy.main:app` works without building settings at import time.
    if name == "app":
        return create_app()
    raise AttributeError(name)
