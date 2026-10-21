"""Repo routes and the GitHub connection endpoint (respx-mocked GitHub, rolled-back DB)."""

import json
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings
from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Connection, Event, Job, Repo
from linkedbuddy.http import make_http_client
from linkedbuddy.main import create_app
from linkedbuddy.security.auth import create_session
from linkedbuddy.security.crypto import decrypt

API = "https://api.github.com"
CSRF = {"X-Requested-With": "linkedbuddy"}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
TOKEN = "github_pat_" + "Ab3" * 10
FERNET_KEY = Fernet.generate_key().decode()
GH = "github"


@pytest.fixture
async def api(db: AsyncSession, settings: Settings) -> AsyncIterator[AsyncClient]:
    """Logged-in client. No env GitHub token, so only a `connections` row counts."""
    app = create_app(
        settings.model_copy(
            update={"app_encryption_key": SecretStr(FERNET_KEY), "github_token": None}
        )
    )

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = _session
    async with make_http_client() as http:
        app.state.http = http  # lifespan does not run under ASGITransport
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=CSRF
        ) as c:
            c.cookies.set("lb_session", await create_session(db))
            yield c


async def make_repo(db: AsyncSession, **kw: Any) -> Repo:
    values: dict[str, Any] = {
        "github_repo_id": random.randint(10**9, 10**12),
        "full_name": "octo/app",
        "visibility": "public",
        "mode": "always_suggest",
        "default_branch": "main",
        "never_send_to_llm": False,
    } | kw
    repo = Repo(**values)
    db.add(repo)
    await db.commit()
    return repo


async def test_put_github_token_stored_encrypted_never_echoed(
    api: AsyncClient, db: AsyncSession
) -> None:
    bad = "ghp_short secret"
    resp = await api.put("/api/connections/github", json={"token": bad})
    assert resp.status_code == 422
    assert "secret" not in resp.text

    resp = await api.put("/api/connections/github", json={"token": TOKEN})
    assert resp.status_code == 204
    assert resp.content == b""
    enc = await db.scalar(select(Connection.access_token_enc).where(Connection.provider == GH))
    assert enc is not None
    assert TOKEN.encode() not in enc
    assert decrypt(enc, key=FERNET_KEY) == TOKEN

    other = TOKEN[:-1] + "Z"
    assert (await api.put("/api/connections/github", json={"token": other})).status_code == 204
    enc = await db.scalar(select(Connection.access_token_enc).where(Connection.provider == GH))
    assert enc is not None
    assert decrypt(enc, key=FERNET_KEY) == other  # replaced, still one row


async def test_sync_without_token_409(api: AsyncClient) -> None:
    resp = await api.post("/api/repos/sync")
    assert resp.status_code == 409
    assert resp.json() == {"detail": "github not connected"}


@respx.mock
async def test_sync_upserts_and_lists(api: AsyncClient, db: AsyncSession) -> None:
    remote = json.loads((FIXTURES / "repos.json").read_text())
    fresh_id, old_id = random.sample(range(10**9, 10**12), 2)
    remote[0] |= {"id": fresh_id, "created_at": (datetime.now(UTC) - timedelta(days=5)).isoformat()}
    remote[1] |= {"id": old_id}
    route = respx.get(f"{API}/user/repos").respond(200, json=remote)
    assert (await api.put("/api/connections/github", json={"token": TOKEN})).status_code == 204

    resp = await api.post("/api/repos/sync")
    assert resp.status_code == 200
    assert resp.json() == {"synced": 2}
    assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"

    fresh = await db.scalar(select(Repo).where(Repo.github_repo_id == fresh_id))
    old = await db.scalar(select(Repo).where(Repo.github_repo_id == old_id))
    assert fresh is not None and old is not None
    assert (fresh.mode, fresh.visibility, fresh.never_send_to_llm) == (
        "always_suggest",
        "public",
        False,
    )
    assert (old.mode, old.visibility, old.never_send_to_llm, old.default_branch) == (
        "releases_and_large",
        "private",
        True,
        "trunk",
    )
    [event] = (await db.scalars(select(Event).where(Event.repo_id.in_([fresh.id, old.id])))).all()
    assert (event.type, event.dedupe_key, event.status) == (
        "new_repo",
        f"new_repo:{fresh_id}",
        "suggested",
    )
    assert await db.scalar(select(Job.id).where(Job.dedupe_key == f"rate:{event.id}"))

    # Re-sync keeps the owner's choices and creates nothing new.
    patch = {"mode": "muted"}
    assert (await api.patch(f"/api/repos/{fresh.id}", json=patch)).status_code == 200
    assert (await api.post("/api/repos/sync")).json() == {"synced": 2}
    await db.refresh(fresh)
    assert fresh.mode == "muted"
    events = (await db.scalars(select(Event.id).where(Event.repo_id == fresh.id))).all()
    assert len(events) == 1

    listed = (await api.get("/api/repos")).json()
    mine = {r["id"]: r for r in listed if r["id"] in {str(fresh.id), str(old.id)}}
    assert mine[str(old.id)] == {
        "id": str(old.id),
        "full_name": "octo/old-secret",
        "visibility": "private",
        "mode": "releases_and_large",
        "never_send_to_llm": True,
        "demo_url": None,
        "last_polled_at": None,
    }


async def test_patch_private_requires_ack(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db, visibility="private", never_send_to_llm=True)
    url = f"/api/repos/{repo.id}"

    resp = await api.patch(url, json={"never_send_to_llm": False})
    assert resp.status_code == 422
    await db.refresh(repo)
    assert repo.never_send_to_llm is True

    resp = await api.patch(
        url, json={"never_send_to_llm": False, "acknowledge_free_tier_data_use": True}
    )
    assert resp.status_code == 200
    assert resp.json()["never_send_to_llm"] is False


async def test_patch_demo_url_https_only(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    url = f"/api/repos/{repo.id}"

    for bad in ("http://demo.example.com", "javascript:alert(1)", "https://", "x" * 501):
        assert (await api.patch(url, json={"demo_url": bad})).status_code == 422
    resp = await api.patch(url, json={"demo_url": "https://demo.example.com/app", "mode": "muted"})
    assert resp.status_code == 200
    assert resp.json()["demo_url"] == "https://demo.example.com/app"
    assert resp.json()["mode"] == "muted"
    resp = await api.patch(url, json={"demo_url": None})
    assert resp.json()["demo_url"] is None
    assert resp.json()["mode"] == "muted"  # absent fields are left alone


async def test_manual_event_created(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)

    resp = await api.post(
        f"/api/repos/{repo.id}/events", json={"base_sha": "a" * 40, "head_sha": "b" * 40}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert (body["type"], body["status"], body["repo_id"]) == ("manual", "suggested", str(repo.id))
    assert (body["base_sha"], body["head_sha"]) == ("a" * 40, "b" * 40)
    event = await db.get(Event, body["id"])
    assert event is not None
    assert await db.scalar(select(Job.id).where(Job.dedupe_key == f"rate:{event.id}"))
    bad = await api.post(f"/api/repos/{repo.id}/events", json={"head_sha": "zz; drop"})
    assert bad.status_code == 422


async def test_poll_route_enqueues_once(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)

    first = await api.post(f"/api/repos/{repo.id}/poll")
    second = await api.post(f"/api/repos/{repo.id}/poll")

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    job = await db.get(Job, first.json()["job_id"])
    assert job is not None
    assert (job.kind, job.payload, job.dedupe_key) == (
        "poll_repo",
        {"repo_id": str(repo.id)},
        f"poll:{repo.id}",
    )


async def test_unknown_repo_404(api: AsyncClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert (await api.post(f"/api/repos/{missing}/poll")).status_code == 404
    assert (await api.patch(f"/api/repos/{missing}", json={"mode": "muted"})).status_code == 404
    assert (await api.post(f"/api/repos/{missing}/events", json={})).status_code == 404
