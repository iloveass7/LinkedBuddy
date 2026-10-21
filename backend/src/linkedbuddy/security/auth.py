"""Owner authentication: password check, server-side sessions, login rate limit (SECURITY.md 2)."""

import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import AuthSession

SESSION_COOKIE = "lb_session"
SESSION_TTL = timedelta(days=14)
PUBLIC_ROUTES = {("GET", "/api/health"), ("POST", "/api/auth/login")}

MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW_S = 15 * 60

_hasher = PasswordHasher()


def verify_password(stored_hash: str, password: str) -> bool:
    """Argon2 verify. A wrong password and a malformed stored hash both mean "no"."""
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        # VerificationError covers VerifyMismatchError; InvalidHashError is not a subclass of it.
        return False


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def create_session(db: AsyncSession) -> str:
    """Store a new session and return the raw token. Only its SHA-256 hash is persisted."""
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=hash_token(token), expires_at=datetime.now(UTC) + SESSION_TTL))
    await db.commit()
    return token


class LoginRateLimiter:
    """In-memory failed-login counter per client IP over a sliding window."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_failures: int = MAX_LOGIN_FAILURES,
        window_s: float = LOGIN_WINDOW_S,
    ) -> None:
        self._clock = clock
        self._max = max_failures
        self._window = window_s
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, ip: str) -> deque[float]:
        failures = self._failures.get(ip)
        if failures is None:
            return deque()
        cutoff = self._clock() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        if not failures:
            del self._failures[ip]
        return failures

    def blocked(self, ip: str) -> bool:
        return len(self._recent(ip)) >= self._max

    def record_failure(self, ip: str) -> None:
        self._recent(ip)
        self._failures.setdefault(ip, deque()).append(self._clock())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


async def require_owner(
    request: Request, db: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    """Router-level guard: 401 unless the request carries a live session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        found = await db.scalar(
            select(AuthSession.token_hash).where(
                AuthSession.token_hash == hash_token(token),
                AuthSession.expires_at > datetime.now(UTC),
            )
        )
        if found is not None:
            return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
