"""poll_repo against respx-mocked GitHub and the rolled-back test DB."""

import json
import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.db.models import Commit, Event, Job, Repo
from linkedbuddy.github.ingest import poll_repo
from linkedbuddy.http import make_http_client
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer
from linkedbuddy.llm.fake import FakeLLM

API = "https://api.github.com"
REPO = "octo/app"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "github"
HEAD = "3" * 40
BASE = "9" * 40  # last commit seen by the previous poll; not in the listing (since filter)
OK = {"x-ratelimit-remaining": "4000", "x-ratelimit-reset": "1900000000"}


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


DETAILS: dict[str, Any] = load("commit_details.json")


@pytest.fixture
async def ctx(db: AsyncSession, settings: Settings) -> AsyncIterator[JobContext]:
    """Handler context whose sessions are savepoints inside the test's rolled-back transaction."""
    conn = await db.connection()
    maker = async_sessionmaker(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    gh_settings = settings.model_copy(
        update={
            "github_token": SecretStr("ghp_" + "t" * 36),
            "app_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    async with make_http_client() as http:
        yield JobContext(maker, http, FakeLLM({}), gh_settings)


async def make_repo(db: AsyncSession, **kw: Any) -> Repo:
    values: dict[str, Any] = {
        "github_repo_id": random.randint(10**9, 10**12),
        "full_name": REPO,
        "visibility": "public",
        "mode": "always_suggest",
        "default_branch": "main",
        "never_send_to_llm": False,
        "last_seen_sha": BASE,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    } | kw
    repo = Repo(**values)
    db.add(repo)
    await db.commit()
    return repo


def detail_route(sha_overrides: dict[str, Any] | None = None) -> respx.Route:
    def respond(request: httpx.Request) -> httpx.Response:
        sha = request.url.path.rsplit("/", 1)[1]
        body = (sha_overrides or {}).get(sha) or DETAILS[sha]
        return httpx.Response(200, json=body, headers=OK)

    return respx.get(url__regex=rf"^{API}/repos/{REPO}/commits/[0-9a-f]{{40}}$").mock(
        side_effect=respond
    )


def mock_github(
    commits: list[Any] | None = None,
    *,
    compare_status: int = 200,
    releases: list[Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, respx.Route]:
    return {
        "list": respx.get(f"{API}/repos/{REPO}/commits").respond(
            200,
            json=load("commits_list.json") if commits is None else commits,
            headers={**OK, "etag": '"e1"'},
        ),
        "detail": detail_route(details),
        "compare": respx.get(url__regex=rf"^{API}/repos/{REPO}/compare/").respond(
            compare_status, json={"commits": []}, headers=OK
        ),
        "releases": respx.get(f"{API}/repos/{REPO}/releases").respond(
            200, json=releases or [], headers=OK
        ),
    }


async def run(ctx: JobContext, repo: Repo) -> dict[str, Any]:
    return await HANDLERS["poll_repo"](ctx, {"repo_id": str(repo.id)})


async def events_of(db: AsyncSession, repo: Repo) -> list[Event]:
    rows = await db.scalars(
        select(Event).where(Event.repo_id == repo.id).execution_options(populate_existing=True)
    )
    return list(rows)


async def rate_jobs(db: AsyncSession, events: list[Event]) -> int:
    keys = [f"rate:{e.id}" for e in events]
    return await db.scalar(select(func.count()).select_from(Job).where(Job.dedupe_key.in_(keys)))


async def fresh(db: AsyncSession, repo: Repo) -> Repo:
    await db.refresh(repo)
    return repo


def test_poll_repo_registered() -> None:
    assert HANDLERS["poll_repo"] is poll_repo


@respx.mock
async def test_poll_idempotent(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db)
    routes = mock_github()

    first = await run(ctx, repo)
    second = await run(ctx, repo)

    assert first["events_created"] == 1
    assert second["events_created"] == 0
    assert routes["detail"].call_count == 3  # the second poll stops at last_seen_sha
    assert routes["list"].calls.last.request.headers["if-none-match"] == '"e1"'
    [event] = await events_of(db, repo)
    assert event.type == "push_group"
    assert event.dedupe_key == f"push:{repo.id}:{HEAD}"
    assert (event.base_sha, event.head_sha) == (BASE, HEAD)
    assert event.status == "suggested"
    assert event.stats == {
        "commits": 3,
        "files": 4,
        "additions": 535,
        "deletions": 303,
        "meaningful": 138,
        "kinds": {"feature": 1, "fix": 1, "noise": 1},
        "truncated": False,
    }
    assert event.heuristic_score > 0
    commits = (await db.scalars(select(Commit).where(Commit.repo_id == repo.id))).all()
    assert len(commits) == 3
    assert {c.event_id for c in commits} == {event.id}
    assert await rate_jobs(db, [event]) == 1
    repo = await fresh(db, repo)
    assert (repo.etag, repo.last_seen_sha) == ('"e1"', HEAD)
    assert repo.last_polled_at is not None


@respx.mock
async def test_poll_304_no_events(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, etag='"e0"')
    routes = mock_github()
    routes["list"].respond(304, headers=OK)

    assert await run(ctx, repo) == {"not_modified": True}

    assert await events_of(db, repo) == []
    assert routes["detail"].call_count == 0
    assert routes["releases"].call_count == 0
    repo = await fresh(db, repo)
    assert repo.last_polled_at is not None
    assert (repo.etag, repo.last_seen_sha) == ('"e0"', BASE)


@respx.mock
async def test_poll_empty_repo_409(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, last_seen_sha=None)
    mock_github()["list"].respond(409, json={"message": "Git Repository is empty."}, headers=OK)

    await run(ctx, repo)

    assert await events_of(db, repo) == []
    repo = await fresh(db, repo)
    assert repo.last_polled_at is not None
    assert repo.last_seen_sha is None


@respx.mock
async def test_poll_force_push_base_none(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db)
    routes = mock_github(compare_status=404)

    await run(ctx, repo)

    [event] = await events_of(db, repo)
    assert event.base_sha is None
    assert event.head_sha == HEAD
    assert routes["compare"].call_count == 1


@respx.mock
async def test_poll_caps_100_commits(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db)
    refs = [{"sha": f"{i:040x}", "commit": {"committer": {"date": None}}} for i in range(1, 151)]
    tmpl = DETAILS[HEAD]
    details = {r["sha"]: {**tmpl, "sha": r["sha"], "commit": {"message": "feat: x"}} for r in refs}
    routes = mock_github(refs, details=details)

    result = await run(ctx, repo)

    assert routes["detail"].call_count == 100
    assert result["commits"] == 100
    [event] = await events_of(db, repo)
    assert event.stats["truncated"] is True
    assert event.stats["commits"] == 100
    assert event.head_sha == refs[0]["sha"]


@respx.mock
async def test_noise_only_not_suggested(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db)
    mock_github(load("commits_noise.json"))

    await run(ctx, repo)

    [event] = await events_of(db, repo)
    assert event.status == "new"
    assert event.heuristic_score == 0
    assert await rate_jobs(db, [event]) == 0


@respx.mock
async def test_private_repo_not_rated(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, visibility="private", never_send_to_llm=True)
    mock_github()

    await run(ctx, repo)

    [event] = await events_of(db, repo)
    assert event.status == "suggested"
    assert await rate_jobs(db, [event]) == 0


@respx.mock
async def test_rate_limited_defers(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db)
    routes = mock_github()
    routes["list"].respond(
        200, json=[], headers={"x-ratelimit-remaining": "10", "x-ratelimit-reset": "1900000000"}
    )

    with pytest.raises(Defer) as info:
        await run(ctx, repo)

    assert info.value.until == datetime.fromtimestamp(1900000000, UTC)
    assert (await fresh(db, repo)).last_polled_at is not None  # set before any GitHub call


@respx.mock
async def test_first_poll_of_existing_repo_records_head_only(
    ctx: JobContext, db: AsyncSession
) -> None:
    repo = await make_repo(db, last_seen_sha=None)
    routes = mock_github()

    result = await run(ctx, repo)

    assert result["events_created"] == 0
    assert routes["detail"].call_count == 0
    assert await events_of(db, repo) == []
    assert (await fresh(db, repo)).last_seen_sha == HEAD


@respx.mock
async def test_new_repo_first_poll_groups_history(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, last_seen_sha=None)
    db.add(
        Event(
            repo_id=repo.id,
            type="new_repo",
            dedupe_key=f"new_repo:{repo.github_repo_id}",
            status="suggested",
        )
    )
    await db.commit()
    routes = mock_github()

    await run(ctx, repo)

    push = [e for e in await events_of(db, repo) if e.type == "push_group"]
    assert len(push) == 1
    assert push[0].base_sha is None
    assert routes["compare"].call_count == 0


@respx.mock
async def test_labeled_trigger_and_file_cap(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, mode="releases_and_large")
    many = [
        {"filename": f"src/m{i}.py", "additions": 0, "deletions": 0, "status": "added"}
        for i in range(301)
    ]
    labeled = {**DETAILS[HEAD], "files": many, "commit": {"message": "feat: big [post]"}}
    mock_github(load("commits_list.json")[:1], details={HEAD: labeled})

    await run(ctx, repo)

    [event] = await events_of(db, repo)  # labeled replaces push_group, no second event
    assert event.type == "labeled"
    assert event.status == "suggested"
    assert event.stats["truncated"] is True
    assert event.stats["files"] == 300
    commit = await db.get(Commit, (repo.id, HEAD))
    assert commit is not None
    assert len(commit.files) == 300


@respx.mock
async def test_post_trailer_labels_event(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, mode="releases_and_large")
    sha = "d" * 40
    mock_github([{"sha": sha, "commit": {"committer": {"date": None}}}])

    await run(ctx, repo)

    [event] = await events_of(db, repo)
    assert event.type == "labeled"
    assert event.status == "suggested"


@respx.mock
async def test_new_releases_create_events_once(ctx: JobContext, db: AsyncSession) -> None:
    repo = await make_repo(db, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    mock_github([], releases=load("releases.json"))

    first = await run(ctx, repo)
    await run(ctx, repo)

    assert first["events_created"] == 1
    [event] = await events_of(db, repo)  # draft and pre-sync releases are skipped
    assert event.type == "release"
    assert event.dedupe_key == f"release:{repo.id}:v1.0.0"
    assert event.status == "suggested"
    assert event.heuristic_score == 4
    assert await rate_jobs(db, [event]) == 1


async def test_missing_repo_is_skipped(ctx: JobContext) -> None:
    assert await poll_repo(ctx, {"repo_id": str(uuid.uuid4())}) == {"skipped": "repo not found"}
