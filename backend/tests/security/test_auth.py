import hashlib
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import OWNER_PASSWORD
from linkedbuddy.config import Settings
from linkedbuddy.db.models import AuthSession, Connection
from linkedbuddy.security.auth import LoginRateLimiter

CSRF = {"X-Requested-With": "linkedbuddy"}


async def login(client: AsyncClient, password: str = OWNER_PASSWORD):  # noqa: ANN201
    return await client.post("/api/auth/login", json={"password": password}, headers=CSRF)


async def session_row(db: AsyncSession, token: str) -> AuthSession | None:
    """This login's row, by token hash: other committed rows in the shared test DB are not ours."""
    digest = hashlib.sha256(token.encode()).digest()
    return await db.scalar(select(AuthSession).where(AuthSession.token_hash == digest))


async def test_login_sets_cookie(client: AsyncClient, db: AsyncSession) -> None:
    resp = await login(client)
    assert resp.status_code == 204
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "SameSite=Strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie  # dev
    token = client.cookies["lb_session"]
    row = await session_row(db, token)
    assert row is not None
    assert row.token_hash == hashlib.sha256(token.encode()).digest()
    assert row.token_hash != token.encode()
    assert timedelta(days=13) < row.expires_at - datetime.now(UTC) <= timedelta(days=14)


async def test_session_lookup_ignores_unrelated_rows(client: AsyncClient, db: AsyncSession) -> None:
    """Regression: a stray committed session (e.g. leaked by another test) must not confuse us."""
    stray = hashlib.sha256(b"stray-token").digest()
    db.add(AuthSession(token_hash=stray, expires_at=datetime.now(UTC) + timedelta(days=1)))
    await db.flush()
    assert (await login(client)).status_code == 204
    token = client.cookies["lb_session"]
    row = await session_row(db, token)
    assert row is not None
    assert row.token_hash == hashlib.sha256(token.encode()).digest() != stray
    assert await db.scalar(select(func.count()).select_from(AuthSession)) >= 2


async def test_login_wrong_password_same_error(client: AsyncClient) -> None:
    resp = await login(client, "wrong")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid credentials"}
    assert "set-cookie" not in resp.headers


async def test_login_malformed_stored_hash_is_401(
    settings: Settings, client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]
) -> None:
    bad = settings.model_copy(update={"owner_password_hash": SecretStr("not-set-yet")})
    async with client_for(bad) as c:
        resp = await login(c)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid credentials"}


async def test_login_rate_limited(client: AsyncClient) -> None:
    for _ in range(5):
        assert (await login(client, "wrong")).status_code == 401
    assert (await login(client, "wrong")).status_code == 429
    assert (await login(client)).status_code == 429  # even the right password, while blocked


def test_rate_limiter_window() -> None:
    now = [0.0]
    limiter = LoginRateLimiter(clock=lambda: now[0])
    for _ in range(5):
        limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4")
    assert not limiter.blocked("5.6.7.8")
    now[0] = 15 * 60 + 1
    assert not limiter.blocked("1.2.3.4")


async def test_protected_requires_session(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    client.cookies.set("lb_session", "forged-token")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_expired_session_rejected(client: AsyncClient, db: AsyncSession) -> None:
    token = "expired-token"  # noqa: S105
    db.add(
        AuthSession(
            token_hash=hashlib.sha256(token.encode()).digest(),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db.flush()
    client.cookies.set("lb_session", token)
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_csrf_header_required(client: AsyncClient) -> None:
    assert (await login(client)).status_code == 204
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 403
    assert (await client.post("/api/auth/logout", headers=CSRF)).status_code == 204


async def test_csrf_header_required_on_login(plain_client: AsyncClient) -> None:
    resp = await plain_client.post("/api/auth/login", json={"password": "x"})
    assert resp.status_code == 403


async def test_logout_deletes_session(client: AsyncClient, db: AsyncSession) -> None:
    await login(client)
    token = client.cookies["lb_session"]
    assert await session_row(db, token) is not None
    assert (await client.post("/api/auth/logout", headers=CSRF)).status_code == 204
    assert await session_row(db, token) is None
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_flags(
    settings: Settings,
    client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]],
    db: AsyncSession,
) -> None:
    async with client_for(settings) as c:
        await login(c)
        assert (await c.get("/api/auth/me")).json() == {
            "github_connected": False,
            "gemini_configured": False,
            "brag_configured": False,
        }
        db.add(Connection(provider="github", access_token_enc=b"x"))
        await db.flush()
        assert (await c.get("/api/auth/me")).json()["github_connected"] is True
    configured = settings.model_copy(
        update={
            "github_token": SecretStr("ghp_x"),
            "gemini_api_key": SecretStr("k"),
            "brag_command": ["brag"],
        }
    )
    blank = settings.model_copy(
        update={"github_token": SecretStr(" "), "gemini_api_key": SecretStr("")}
    )
    await db.execute(Connection.__table__.delete())
    async with client_for(configured) as c:
        await login(c)
        assert (await c.get("/api/auth/me")).json() == {
            "github_connected": True,
            "gemini_configured": True,
            "brag_configured": True,
        }
    async with client_for(blank) as c:
        await login(c)
        me = (await c.get("/api/auth/me")).json()
        assert me["github_connected"] is False
        assert me["gemini_configured"] is False


async def test_security_headers(plain_client: AsyncClient) -> None:
    resp = await plain_client.get("/nope")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in resp.headers  # dev


async def test_prod_hsts_and_secure_cookie(
    settings: Settings, client_for: Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]
) -> None:
    async with client_for(settings.model_copy(update={"app_env": "prod"})) as c:
        resp = await login(c)
    assert re.search(r"; Secure", resp.headers["set-cookie"])
    assert "max-age=" in resp.headers["strict-transport-security"]


async def test_cors_only_allowed_origins(plain_client: AsyncClient) -> None:
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-requested-with,content-type",
    }
    ok = await plain_client.options(
        "/api/auth/login", headers={"Origin": "http://localhost:5173", **preflight}
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = await plain_client.options(
        "/api/auth/login", headers={"Origin": "http://evil.example", **preflight}
    )
    assert "access-control-allow-origin" not in bad.headers
