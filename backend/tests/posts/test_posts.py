"""Copy-paste publishing, post history and manual metrics."""

import asyncio
import base64
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Decision, Draft, Event, Post, Repo
from linkedbuddy.drafts.context import build_context
from linkedbuddy.enums import PrivacyMode
from linkedbuddy.github.client import GitHubClient
from linkedbuddy.http import make_http_client
from linkedbuddy.main import create_app
from linkedbuddy.security.auth import create_session

CSRF = {"X-Requested-With": "linkedbuddy"}
BASE_SHA = "b" * 40
HEAD_SHA = "a" * 40
T0 = datetime(2026, 1, 1, tzinfo=UTC)
OK = {"x-ratelimit-remaining": "4000", "x-ratelimit-reset": "1900000000"}
AFTER_AUTH_IGNORED = ("FROM sessions", "SAVEPOINT")


@pytest.fixture
async def api(client: AsyncClient, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    client.headers.update(CSRF)
    client.cookies.set("lb_session", await create_session(db))
    yield client


async def make_repo(db: AsyncSession, full_name: str = "octo/posts") -> Repo:
    repo = Repo(
        github_repo_id=uuid.uuid4().int % 10**12,
        full_name=full_name,
        visibility="public",
        mode="always_suggest",
        default_branch="main",
    )
    db.add(repo)
    await db.commit()
    return repo


async def make_event(db: AsyncSession, repo_id: uuid.UUID, **kw: Any) -> uuid.UUID:
    values = {
        "repo_id": repo_id,
        "type": "push_group",
        "dedupe_key": f"t:{uuid.uuid4()}",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "status": "drafted",
    } | kw
    event_id = await db.scalar(insert(Event).values(**values).returning(Event.id))
    assert event_id is not None
    return event_id


async def make_drafts(
    db: AsyncSession, event_id: uuid.UUID, statuses: list[str], parent: uuid.UUID | None = None
) -> list[uuid.UUID]:
    rows = [
        {
            "event_id": event_id,
            "parent_draft_id": parent,
            "variant_index": i,
            "tone": "casual",
            "body": f"body {i}",
            "model": "m",
            "prompt_version": "1",
            "status": s,
        }
        for i, s in enumerate(statuses)
    ]
    ids = list((await db.scalars(insert(Draft).values(rows).returning(Draft.id))).all())
    await db.commit()
    return ids


async def approved_draft(db: AsyncSession, **event_kw: Any) -> tuple[Repo, uuid.UUID, uuid.UUID]:
    repo = await make_repo(db)
    event_id = await make_event(db, repo.id, **event_kw)
    (draft_id,) = await make_drafts(db, event_id, ["approved"])
    return repo, event_id, draft_id


async def seed_posts(db: AsyncSession, repo_id: uuid.UUID, count: int) -> list[Post]:
    """`count` posts on the repo, one event and draft each, posted_at 1 hour apart."""
    out: list[Post] = []
    for i in range(count):
        event_id = await make_event(db, repo_id)
        (draft_id,) = await make_drafts(db, event_id, ["posted"])
        post = Post(
            draft_id=draft_id,
            publish_method="manual",
            final_text=f"post {i}",
            posted_at=T0 + timedelta(hours=i),
        )
        db.add(post)
        out.append(post)
    await db.commit()
    return out


def publish(api: AsyncClient, draft_id: uuid.UUID, text: str = "final text") -> Any:
    return api.post(
        f"/api/drafts/{draft_id}/publish", json={"final_text": text, "method": "manual"}
    )


async def test_publish_requires_approved(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    event_id = await make_event(db, repo.id)
    drafts = await make_drafts(db, event_id, ["draft", "discarded", "posted"])

    for draft_id in drafts:
        resp = await publish(api, draft_id)
        assert resp.status_code == 409, resp.text

    assert await db.scalar(select(Post.id)) is None


async def test_publish_unknown_draft_404(api: AsyncClient) -> None:
    assert (await publish(api, uuid.uuid4())).status_code == 404


async def test_publish_rejects_redacted_marker(api: AsyncClient, db: AsyncSession) -> None:
    _, _, draft_id = await approved_draft(db)
    secret_text = "I shipped [REDACTED:github_token] today"

    resp = await publish(api, draft_id, secret_text)

    assert resp.status_code == 422
    assert "REDACTED" not in resp.text and "shipped" not in resp.text  # never echoed
    assert await db.scalar(select(Draft.status).where(Draft.id == draft_id)) == "approved"


@pytest.mark.parametrize(
    "text", ["x" * 3001, "", "   \n\t "], ids=["3001_chars", "empty", "whitespace"]
)
async def test_publish_rejects_bad_length_or_blank(
    api: AsyncClient, db: AsyncSession, text: str
) -> None:
    _, _, draft_id = await approved_draft(db)

    assert (await publish(api, draft_id, text)).status_code == 422


async def test_publish_accepts_3000_chars(api: AsyncClient, db: AsyncSession) -> None:
    _, _, draft_id = await approved_draft(db)

    resp = await publish(api, draft_id, "x" * 3000)

    assert resp.status_code == 201
    assert len(resp.json()["final_text"]) == 3000


@pytest.mark.parametrize("payload", [{"method": "api"}, {}, {"method": "manual", "extra": 1}])
async def test_publish_rejects_other_methods_and_extra_fields(
    api: AsyncClient, db: AsyncSession, payload: dict[str, Any]
) -> None:
    _, _, draft_id = await approved_draft(db)

    resp = await api.post(f"/api/drafts/{draft_id}/publish", json={"final_text": "ok"} | payload)

    assert resp.status_code == 422


async def test_publish_records_range_and_statuses(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    event_id = await make_event(db, repo.id)
    other_event = await make_event(db, repo.id)
    (chosen, sib_draft, sib_approved, sib_discarded) = await make_drafts(
        db, event_id, ["approved", "draft", "approved", "discarded"]
    )
    (child,) = await make_drafts(db, event_id, ["draft"], parent=chosen)  # quick-edit child
    (unrelated,) = await make_drafts(db, other_event, ["draft"])

    resp = await publish(api, chosen, "Edited final text")

    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {
        "id", "draft_id", "posted_at", "publish_method", "final_text",
        "covered_base_sha", "covered_head_sha", "metrics",
    }  # fmt: skip
    assert body["draft_id"] == str(chosen)
    assert body["publish_method"] == "manual"
    assert body["final_text"] == "Edited final text"
    assert body["covered_base_sha"] == BASE_SHA
    assert body["covered_head_sha"] == HEAD_SHA
    assert body["metrics"] == {}

    statuses = dict((await db.execute(select(Draft.id, Draft.status))).all())
    assert statuses[chosen] == "posted"
    assert statuses[sib_draft] == statuses[sib_approved] == statuses[child] == "discarded"
    assert statuses[sib_discarded] == "discarded"
    assert statuses[unrelated] == "draft"  # another event is untouched
    assert await db.scalar(select(Event.status).where(Event.id == event_id)) == "posted"
    assert await db.scalar(select(Event.status).where(Event.id == other_event)) == "drafted"
    actions = (await db.scalars(select(Decision.action).where(Decision.event_id == event_id))).all()
    assert list(actions) == ["post"]
    assert await db.scalar(select(Post.draft_id)) == chosen


async def test_publish_twice_returns_409(api: AsyncClient, db: AsyncSession) -> None:
    _, _, draft_id = await approved_draft(db)

    assert (await publish(api, draft_id)).status_code == 201
    assert (await publish(api, draft_id)).status_code == 409

    assert len((await db.scalars(select(Post.id))).all()) == 1


async def test_publish_discarded_sibling_cannot_be_published(
    api: AsyncClient, db: AsyncSession
) -> None:
    repo = await make_repo(db)
    event_id = await make_event(db, repo.id)
    first, second = await make_drafts(db, event_id, ["approved", "approved"])

    assert (await publish(api, first)).status_code == 201
    assert (await publish(api, second)).status_code == 409


@respx.mock
async def test_next_context_includes_previous_post(api: AsyncClient, db: AsyncSession) -> None:
    repo, _, draft_id = await approved_draft(db)
    assert (await publish(api, draft_id, "Shipped the importer last week")).status_code == 201
    new_event = await make_event(db, repo.id, head_sha=None, base_sha=None, status="new")
    respx.get("https://api.github.com/repos/octo/posts/releases").respond(200, json=[], headers=OK)

    async with make_http_client() as http:
        pkg = await build_context(
            db, GitHubClient(http, "test-token"), new_event, PrivacyMode.metadata_only
        )

    prev = [e for e in pkg.evidence if e.id == "prev_post"]
    assert [e.text for e in prev] == ["Shipped the importer last week"]


async def test_metrics_patch_merges_only_provided(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    (post,) = await seed_posts(db, repo.id, 1)

    first = await api.patch(
        f"/api/posts/{post.id}/metrics", json={"impressions": 120, "reactions": 7}
    )
    second = await api.patch(f"/api/posts/{post.id}/metrics", json={"reactions": 9, "saves": 0})

    assert first.status_code == 200
    assert first.json()["metrics"] == {"impressions": 120, "reactions": 7}
    assert second.status_code == 200
    out = second.json()
    assert out["metrics"] == {"impressions": 120, "reactions": 9, "saves": 0}
    assert out["id"] == str(post.id) and out["final_text"] == "post 0"
    await db.refresh(post)
    assert post.metrics == out["metrics"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"impressions": -1},
        {"impressions": 10**9 + 1},
        {"impressions": 1.5},
        {"impressions": "12"},
        {"impressions": True},
        {"impressions": None},
        {"likes": 3},
    ],
)
async def test_metrics_patch_rejects_bad_body(
    api: AsyncClient, db: AsyncSession, payload: dict[str, Any]
) -> None:
    repo = await make_repo(db)
    (post,) = await seed_posts(db, repo.id, 1)

    resp = await api.patch(f"/api/posts/{post.id}/metrics", json=payload)

    assert resp.status_code == 422
    await db.refresh(post)
    assert post.metrics == {}


async def test_metrics_patch_accepts_upper_bound(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    (post,) = await seed_posts(db, repo.id, 1)

    resp = await api.patch(f"/api/posts/{post.id}/metrics", json={"impressions": 10**9})

    assert resp.status_code == 200


async def test_metrics_patch_unknown_post_404(api: AsyncClient) -> None:
    resp = await api.patch(f"/api/posts/{uuid.uuid4()}/metrics", json={"saves": 1})

    assert resp.status_code == 404


async def test_list_posts_newest_first_and_paginates(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    other = await make_repo(db, "octo/other")
    posts = await seed_posts(db, repo.id, 5)
    await seed_posts(db, other.id, 2)
    expected = [str(p.id) for p in reversed(posts)]

    first = (await api.get(f"/api/repos/{repo.id}/posts?limit=2")).json()
    second = (
        await api.get(f"/api/repos/{repo.id}/posts?limit=2&cursor={first['next_cursor']}")
    ).json()
    third = (
        await api.get(f"/api/repos/{repo.id}/posts?limit=2&cursor={second['next_cursor']}")
    ).json()

    assert [p["id"] for p in first["items"]] == expected[:2]
    assert [p["id"] for p in second["items"]] == expected[2:4]
    assert [p["id"] for p in third["items"]] == expected[4:]
    assert third["next_cursor"] is None
    assert set(first["items"][0]) >= {"id", "draft_id", "posted_at", "final_text", "metrics"}


async def test_list_posts_same_timestamp_has_no_gaps(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    posts = await seed_posts(db, repo.id, 5)
    for p in posts:
        p.posted_at = T0
    await db.commit()

    seen: list[str] = []
    cursor = ""
    for _ in range(5):
        page = (await api.get(f"/api/repos/{repo.id}/posts?limit=2{cursor}")).json()
        seen += [p["id"] for p in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"

    assert len(seen) == len(set(seen)) == 5
    assert seen == [str(i) for i in sorted((p.id for p in posts), reverse=True)]


async def test_list_posts_empty_and_unknown_repo(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)

    empty = await api.get(f"/api/repos/{repo.id}/posts")
    unknown = await api.get(f"/api/repos/{uuid.uuid4()}/posts")

    assert empty.status_code == 200
    assert empty.json() == {"items": [], "next_cursor": None}
    assert unknown.status_code == 404


async def test_list_posts_empty_with_cursor_past_end(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    await seed_posts(db, repo.id, 1)
    page = (await api.get(f"/api/repos/{repo.id}/posts?limit=1")).json()
    assert page["next_cursor"] is None
    cursor = base64.urlsafe_b64encode(
        f'["{(T0 - timedelta(days=1)).isoformat()}", "{uuid.uuid4()}"]'.encode()
    ).decode()

    resp = await api.get(f"/api/repos/{repo.id}/posts?cursor={cursor}")

    assert resp.status_code == 200 and resp.json()["items"] == []  # repo exists: not a 404


@pytest.mark.parametrize("query", ["cursor=garbage", "limit=0", "limit=101"])
async def test_list_posts_rejects_bad_query(api: AsyncClient, db: AsyncSession, query: str) -> None:
    repo = await make_repo(db)

    assert (await api.get(f"/api/repos/{repo.id}/posts?{query}")).status_code == 422


async def test_list_posts_single_query(
    api: AsyncClient, db: AsyncSession, count_queries: list[str]
) -> None:
    repo = await make_repo(db)
    await seed_posts(db, repo.id, 3)
    count_queries.clear()

    resp = await api.get(f"/api/repos/{repo.id}/posts")

    assert resp.status_code == 200 and len(resp.json()["items"]) == 3
    after_auth = [s for s in count_queries if not any(k in s for k in AFTER_AUTH_IGNORED)]
    assert len(after_auth) == 1, after_auth


async def test_concurrent_publish_creates_one_post(
    committed: Any, engine: Any, settings: Any
) -> None:
    """Two simultaneous publishes of one draft: one 201, one 409, one post row."""
    maker = committed
    async with maker() as s:
        repo = await make_repo(s)
        event_id = await make_event(s, repo.id)
        (draft_id,) = await make_drafts(s, event_id, ["approved"])
        token = await create_session(s)
    app = create_app(settings)
    app.state.sessionmaker = maker
    try:
        async with AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
            headers=CSRF, cookies={"lb_session": token},
        ) as c:  # fmt: skip
            results = await asyncio.gather(publish(c, draft_id), publish(c, draft_id))
        assert sorted(r.status_code for r in results) == [201, 409]
        async with maker() as s:
            assert (
                len((await s.scalars(select(Post.id).where(Post.draft_id == draft_id))).all()) == 1
            )
    finally:
        async with maker() as s:
            await s.delete(await s.get(Repo, repo.id))
            await s.commit()
