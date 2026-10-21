"""Owner login, logout, and status (SECURITY.md 2)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import AuthSession, Connection
from linkedbuddy.security.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    LoginRateLimiter,
    create_session,
    hash_token,
    verify_password,
)

# Mounted outside the owner guard: login is how a session starts.
public_router = APIRouter(prefix="/auth", tags=["auth"])
# Mounted behind `require_owner`.
router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_FLAGS = {"path": "/", "httponly": True, "samesite": "strict"}


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(max_length=200)


class MeResponse(BaseModel):
    github_connected: bool
    gemini_configured: bool
    brag_configured: bool


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _nonblank(secret: object | None) -> bool:
    value = getattr(secret, "get_secret_value", lambda: "")()
    return bool(value.strip())


@public_router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(
    body: LoginBody,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    settings = request.app.state.settings
    limiter: LoginRateLimiter = request.app.state.login_limiter
    ip = _client_ip(request)
    if limiter.blocked(ip):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")
    if not verify_password(settings.owner_password_hash.get_secret_value(), body.password):
        limiter.record_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    limiter.reset(ip)
    token = await create_session(db)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        secure=settings.app_env != "dev",
        **_COOKIE_FLAGS,  # type: ignore[arg-type]
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    token = request.cookies.get(SESSION_COOKIE)  # require_owner already proved it is valid
    if token:
        await db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        await db.commit()
    response.delete_cookie(
        SESSION_COOKIE,
        secure=request.app.state.settings.app_env != "dev",
        **_COOKIE_FLAGS,  # type: ignore[arg-type]
    )


@router.get("/me", response_model=MeResponse)
async def me(request: Request, db: Annotated[AsyncSession, Depends(get_session)]) -> MeResponse:
    settings = request.app.state.settings
    has_row = await db.scalar(select(Connection.id).where(Connection.provider == "github"))
    return MeResponse(
        github_connected=has_row is not None or _nonblank(settings.github_token),
        gemini_configured=_nonblank(settings.gemini_api_key),
        brag_configured=bool(settings.brag_command),
    )
