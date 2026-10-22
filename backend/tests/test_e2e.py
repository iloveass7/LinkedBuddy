"""End to end: GitHub activity becomes a posted LinkedIn draft through the real routes and handlers.

GitHub is respx, Gemini is FakeLLM and the Mermaid renderer is patched to write a tiny PNG. Jobs
run in-process (claim, handler, complete, as the worker does). Handlers open their own sessions,
so this test really commits; it deletes everything it created when it ends.
"""

import io
import json
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import OWNER_PASSWORD
from linkedbuddy import scheduler
from linkedbuddy.config import Settings
from linkedbuddy.db.models import AuthSession, Connection, Repo
from linkedbuddy.drafts.validate import DraftResult, Segment, Variant
from linkedbuddy.enums import ConnectionProvider
from linkedbuddy.http import make_http_client
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import claim, complete
from linkedbuddy.llm.fake import FakeLLM
from linkedbuddy.main import create_app
from linkedbuddy.media import mermaid
from linkedbuddy.triage.rate import RatingResult, RatingScores

API = "https://api.github.com"
REPO = "octo/e2e-dashboard"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"
HEAD = "3" * 40  # newest commit in commits_list.json
OK = {"x-ratelimit-remaining": "4000", "x-ratelimit-reset": "1900000000"}
CSRF = {"X-Requested-With": "linkedbuddy"}
TOKEN = "github_pat_" + "E2e" * 10
README = "# Usage dashboard\n\nCharts daily active users per feature."
MERMAID = 'flowchart LR\n  events --> d["src/dashboard.py"] --> chart'
MAX_JOBS = 20


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def rating() -> RatingResult:
    return RatingResult(
        reason="A new dashboard with a visible chart.",
        scores=RatingScores(
            visible_outcome=5, demonstrability=5, novelty=4, completeness=4, freshness=5, goal_fit=4
        ),
        rating="high",
        suggested_angle="Show the chart first.",
        needs_user_assets=[],
    )


def draft_result() -> DraftResult:
    def variant(angle: str, *segments: tuple[str, list[str]]) -> Variant:
        return Variant(
            angle=angle,
            segments=[Segment(text=t, evidence=e) for t, e in segments],
            alt_openings=["Which feature do people actually use?"],
            first_comment=f"Code: github.com/{REPO}",
        )

    return DraftResult(
        variants=[
            variant(
                "problem-first",
                ("I could not tell which features people used.\n\n", ["readme:L3"]),
                ("So I added a usage dashboard in src/dashboard.py.", ["commit:3333333"]),
            ),
            variant("build-log", ("Shipped: a usage dashboard.", ["commit:3333333"])),
        ],
        diagram_mermaid=MERMAID,
        alt_text_diagram="Events flow into the dashboard, which draws a chart.",
    )


def mock_github(github_id: int) -> None:
    """Every GitHub call of the pipeline. Anything else is unmocked and fails the test."""
    repo = {
        "id": github_id,
        "full_name": REPO,
        "default_branch": "main",
        "private": False,
        "fork": False,
        "archived": False,
        "description": "usage dashboard",
        "created_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        "pushed_at": datetime.now(UTC).isoformat(),
    }
    details = load("commit_details.json")
    respx.get(f"{API}/user/repos").respond(200, json=[repo], headers=OK)
    respx.get(f"{API}/repos/{REPO}/commits").respond(
        200, json=load("commits_list.json"), headers={**OK, "etag": '"e2e"'}
    )
    respx.get(url__regex=rf"^{API}/repos/{REPO}/commits/[0-9a-f]{{40}}$").mock(
        side_effect=lambda request: httpx.Response(
            200, json=details[request.url.path.rsplit("/", 1)[1]], headers=OK
        )
    )
    respx.get(f"{API}/repos/{REPO}/releases").respond(200, json=[], headers=OK)
    respx.get(f"{API}/repos/{REPO}/readme").respond(200, text=README, headers=OK)
    tree = [{"path": p, "type": "blob"} for p in ("README.md", "src/dashboard.py", "src/parse.py")]
    respx.get(f"{API}/repos/{REPO}/git/trees/{HEAD}").respond(200, json={"tree": tree}, headers=OK)


async def fake_render(src: str, out: Path, settings: Settings | None = None) -> None:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="PNG")
    out.write_bytes(buf.getvalue())


async def drain(ctx: JobContext) -> list[str]:
    """Run runnable jobs like the worker does until none is left; returns the kinds run.

    A handler error propagates and fails the test.
    """
    ran: list[str] = []
    for _ in range(MAX_JOBS):
        async with ctx.sessionmaker() as db:
            job = await claim(db, sorted(HANDLERS))
            await db.commit()
        if job is None:
            return ran
        result = await HANDLERS[job.kind](ctx, job.payload)
        async with ctx.sessionmaker() as db:
            assert await complete(db, job.id, result, attempts=job.attempts)
            await db.commit()
        ran.append(job.kind)
    raise AssertionError(f"still jobs after {MAX_JOBS}: {ran}")


@pytest.fixture
def e2e_settings(settings: Settings, tmp_path: Path) -> Settings:
    """No env GitHub token, so only the token stored through the API can work."""
    return settings.model_copy(
        update={
            "app_encryption_key": SecretStr(Fernet.generate_key().decode()),
            "github_token": None,
            "gemini_api_key": None,
            "media_root": tmp_path / "media",
        }
    )


@pytest.fixture
async def cleanup(committed: async_sessionmaker[AsyncSession]) -> AsyncIterator[int]:
    """A fresh GitHub repo id; afterwards deletes that repo (cascades), the stored GitHub token and
    the sessions this test created. The `committed` fixture wipes the jobs."""
    async with committed() as s:
        connection = await s.scalar(
            select(Connection.id).where(Connection.provider == ConnectionProvider.github)
        )
        assert connection is None, "the test DB already holds a committed GitHub connection"
        before = set((await s.scalars(select(AuthSession.token_hash))).all())
    github_id = random.randint(10**9, 10**12)
    yield github_id
    async with committed() as s:
        await s.execute(delete(Repo).where(Repo.github_repo_id == github_id))
        await s.execute(delete(Connection).where(Connection.provider == ConnectionProvider.github))
        created = set((await s.scalars(select(AuthSession.token_hash))).all()) - before
        await s.execute(delete(AuthSession).where(AuthSession.token_hash.in_(created)))
        await s.commit()


@respx.mock
async def test_github_to_post(
    committed: async_sessionmaker[AsyncSession],
    cleanup: int,
    e2e_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_github(cleanup)
    monkeypatch.setattr(mermaid, "render_mermaid", fake_render)
    llm = FakeLLM({RatingResult: [rating(), rating()], DraftResult: [draft_result()]})

    app = create_app(e2e_settings)
    app.state.sessionmaker = committed  # lifespan does not run under ASGITransport
    async with make_http_client() as http:
        app.state.http = http
        ctx = JobContext(committed, http, llm, e2e_settings)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers=CSRF
        ) as api:
            resp = await api.post("/api/auth/login", json={"password": OWNER_PASSWORD})
            assert resp.status_code == 204, resp.text

            resp = await api.put("/api/connections/github", json={"token": TOKEN})
            assert resp.status_code == 204, resp.text
            resp = await api.post("/api/repos/sync")
            assert resp.json() == {"synced": 1}

            async with committed() as db:
                report = await scheduler.tick(db, datetime.now(UTC), settings=e2e_settings)
                await db.commit()
            assert report.polls_enqueued == 1, "other committed repos are left in the test DB"

            # rate the new_repo event, poll (one push_group event), rate the push event
            ran = await drain(ctx)
            assert sorted(ran) == ["poll_repo", "rate_event", "rate_event"]

            resp = await api.get("/api/inbox")
            assert resp.status_code == 200
            items = resp.json()["items"]
            push = next(i for i in items if i["type"] == "push_group")
            assert push["repo_full_name"] == REPO
            assert (push["rating"], push["status"]) == ("high", "suggested")
            event_id = push["id"]

            resp = await api.post(f"/api/events/{event_id}/generate", json={})
            assert resp.status_code == 202, resp.text
            assert await drain(ctx) == ["generate_draft", "render_diagram"]

            resp = await api.get(f"/api/events/{event_id}/drafts")
            assert resp.status_code == 200
            drafts = resp.json()
            assert len(drafts) == 2
            assert all(d["unsupported_claims"] == 0 for d in drafts)
            first = next(d for d in drafts if d["variant_index"] == 0)
            [diagram] = first["media"]
            assert (diagram["type"], diagram["status"]) == ("diagram", "ready")
            resp = await api.get(f"/api/media/{diagram['id']}/file")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "image/png"

            resp = await api.patch(f"/api/drafts/{first['id']}", json={"status": "approved"})
            assert resp.status_code == 200 and resp.json()["status"] == "approved"
            resp = await api.post(
                f"/api/drafts/{first['id']}/publish",
                json={"final_text": first["body"], "method": "manual"},
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["covered_head_sha"] == HEAD

            resp = await api.get("/api/inbox", params={"status": "posted"})
            assert [i["id"] for i in resp.json()["items"]] == [event_id]
            assert {c.request.method for c in respx.calls} == {"GET"}
            assert [c.tier for c in llm.calls] == ["fast", "fast", "quality"]
