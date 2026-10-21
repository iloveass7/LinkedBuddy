"""Inbox listing (rated, keyset-paginated, one query) and dismiss/decision logging."""

import base64
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Decision, Event, Repo
from linkedbuddy.inbox.service import log_decision
from linkedbuddy.security.auth import create_session

CSRF = {"X-Requested-With": "linkedbuddy"}
BASE = datetime(2026, 1, 1, tzinfo=UTC)
RANK = {"high": 0, "medium": 1, "low": 2, None: 3}


@pytest.fixture
async def api(client: AsyncClient, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    client.headers.update(CSRF)
    client.cookies.set("lb_session", await create_session(db))
    yield client


async def make_repo(db: AsyncSession, full_name: str = "octo/app") -> Repo:
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


def event_row(repo_id: uuid.UUID, **kw: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "repo_id": repo_id,
        "type": "push_group",
        "dedupe_key": f"k:{uuid.uuid4()}",
        "status": "suggested",
        "created_at": BASE,
        # Multi-row INSERT needs the same keys in every row.
        "rating": None,
        "rating_reason": None,
        "heuristic_score": 0.0,
        "stats": {},
        "context_json": None,
        "rating_json": None,
    } | kw


async def seed(db: AsyncSession, rows: list[dict[str, Any]]) -> None:
    """One INSERT for all rows (the DB round trip is ~0.4 s)."""
    await db.execute(insert(Event).values(rows))
    await db.commit()


def expected_order(rows: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(rows, key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return [str(r["id"]) for r in sorted(ordered, key=lambda r: RANK[r.get("rating")])]


async def test_inbox_sorted_by_rating_then_recency(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    rows = [
        event_row(repo.id, rating=None, created_at=BASE + timedelta(hours=9)),
        event_row(repo.id, rating="low", created_at=BASE + timedelta(hours=8)),
        event_row(repo.id, rating="high", created_at=BASE + timedelta(hours=1)),
        event_row(repo.id, rating="medium", created_at=BASE + timedelta(hours=7)),
        event_row(repo.id, rating="high", created_at=BASE + timedelta(hours=2)),
        event_row(repo.id, rating="medium", created_at=BASE + timedelta(hours=6), status="new"),
    ]
    await seed(db, rows)

    resp = await api.get("/api/inbox")

    assert resp.status_code == 200
    body = resp.json()
    assert [i["id"] for i in body["items"]] == expected_order(rows)
    assert [i["rating"] for i in body["items"]] == [
        "high", "high", "medium", "medium", "low", None,
    ]  # fmt: skip
    assert body["next_cursor"] is None


async def test_inbox_item_shape_and_stats_summary(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db, "octo/shape")
    push = event_row(
        repo.id,
        rating="high",
        rating_reason="ships a visible feature",
        heuristic_score=0.75,
        stats={"commits": 3, "files": 5, "additions": 120, "deletions": 40},
    )
    release = event_row(
        repo.id, type="release", created_at=BASE - timedelta(days=1), stats={"tag": "v1.2.0"}
    )
    bare = event_row(repo.id, created_at=BASE - timedelta(days=2), stats={})
    await seed(db, [push, release, bare])

    items = (await api.get("/api/inbox")).json()["items"]

    assert set(items[0]) == {
        "id", "repo_full_name", "type", "rating", "rating_reason",
        "heuristic_score", "status", "created_at", "stats_summary",
    }  # fmt: skip
    assert items[0]["repo_full_name"] == "octo/shape"
    assert items[0]["rating_reason"] == "ships a visible feature"
    assert items[0]["heuristic_score"] == 0.75
    assert items[0]["stats_summary"] == "3 commits · +120/−40 · 5 files"
    assert items[1]["stats_summary"] == "release v1.2.0"
    assert items[2]["stats_summary"] == ""


async def test_inbox_status_filter(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    rows = [
        event_row(repo.id, status=s, created_at=BASE + timedelta(minutes=i))
        for i, s in enumerate(["suggested", "new", "drafted", "dismissed", "posted", "generating"])
    ]
    await seed(db, rows)

    default = (await api.get("/api/inbox")).json()["items"]
    assert sorted(i["status"] for i in default) == ["drafted", "new", "suggested"]

    dismissed = (await api.get("/api/inbox", params={"status": "dismissed"})).json()["items"]
    assert [i["status"] for i in dismissed] == ["dismissed"]

    assert (await api.get("/api/inbox", params={"status": "bogus"})).status_code == 422


async def test_inbox_limit_bounds_and_bad_cursor(api: AsyncClient) -> None:
    assert (await api.get("/api/inbox", params={"limit": 0})).status_code == 422
    assert (await api.get("/api/inbox", params={"limit": 201})).status_code == 422

    for cursor in ("not-base64!!", base64.urlsafe_b64encode(b"[1]").decode(), "e30="):
        resp = await api.get("/api/inbox", params={"cursor": cursor})
        assert resp.status_code == 422
        assert resp.json() == {"detail": "invalid cursor"}
    bad_rank = base64.urlsafe_b64encode(
        json.dumps([9, BASE.isoformat(), str(uuid.uuid4())]).encode()
    ).decode()
    assert (await api.get("/api/inbox", params={"cursor": bad_rank})).status_code == 422


async def test_inbox_keyset_pagination(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    ratings = ["high", "medium", "low", None]
    # Only 20 distinct timestamps so many rows tie on created_at and the id tiebreak is used.
    rows = [
        event_row(repo.id, rating=ratings[i % 4], created_at=BASE + timedelta(minutes=i % 20))
        for i in range(120)
    ]
    await seed(db, rows)

    seen: list[str] = []
    pages = 0
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        body = (await api.get("/api/inbox", params=params)).json()
        pages += 1
        seen += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert len(seen) == len(set(seen)) == 120  # no duplicates, no gaps
    assert seen == expected_order(rows)


async def test_inbox_excludes_heavy_columns(
    api: AsyncClient, db: AsyncSession, count_queries: list[str]
) -> None:
    repo = await make_repo(db)
    await seed(db, [event_row(repo.id, context_json={"big": "x"}, rating_json={"a": 1})])
    count_queries.clear()

    assert (await api.get("/api/inbox")).status_code == 200

    inbox_sql = [s for s in count_queries if "FROM events" in s]
    assert len(inbox_sql) == 1
    for heavy in ("context_json", "rating_json", "context_hash", "dedupe_key"):
        assert heavy not in inbox_sql[0]


async def test_inbox_single_query(
    api: AsyncClient, db: AsyncSession, count_queries: list[str]
) -> None:
    repo = await make_repo(db)
    await seed(db, [event_row(repo.id, rating="high") for _ in range(120)])
    count_queries.clear()

    resp = await api.get("/api/inbox")

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 50
    # The auth guard's session lookup (and savepoint housekeeping of the rollback fixture) is
    # not the inbox's budget; everything else must be exactly one statement.
    after_auth = [s for s in count_queries if "FROM sessions" not in s and "SAVEPOINT" not in s]
    assert len(after_auth) == 1, after_auth


async def test_dismiss_logs_decision(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    row = event_row(repo.id, rating="medium", heuristic_score=0.5)
    await seed(db, [row])

    resp = await api.post(f"/api/events/{row['id']}/dismiss")

    assert resp.status_code == 204
    assert resp.content == b""
    assert await db.scalar(select(Event.status).where(Event.id == row["id"])) == "dismissed"
    decisions = (await db.scalars(select(Decision).where(Decision.event_id == row["id"]))).all()
    assert len(decisions) == 1
    assert decisions[0].action == "ignore"
    assert decisions[0].rating_at_time == "medium"
    assert decisions[0].heuristic_at_time == 0.5
    listed = (await api.get("/api/inbox")).json()["items"]
    assert row["id"] not in {uuid.UUID(i["id"]) for i in listed}


async def test_dismiss_twice_is_idempotent(api: AsyncClient, db: AsyncSession) -> None:
    repo = await make_repo(db)
    row = event_row(repo.id)
    await seed(db, [row])

    assert (await api.post(f"/api/events/{row['id']}/dismiss")).status_code == 204
    assert (await api.post(f"/api/events/{row['id']}/dismiss")).status_code == 204

    decisions = (await db.scalars(select(Decision).where(Decision.event_id == row["id"]))).all()
    assert len(decisions) == 1


async def test_dismiss_unknown_404_and_posted_409(api: AsyncClient, db: AsyncSession) -> None:
    assert (await api.post(f"/api/events/{uuid.uuid4()}/dismiss")).status_code == 404

    repo = await make_repo(db)
    row = event_row(repo.id, status="posted")
    await seed(db, [row])
    assert (await api.post(f"/api/events/{row['id']}/dismiss")).status_code == 409
    assert await db.scalar(select(Event.status).where(Event.id == row["id"])) == "posted"
    assert await db.scalar(select(Decision.id).where(Decision.event_id == row["id"])) is None


async def test_log_decision_snapshots_rating_and_heuristic(db: AsyncSession) -> None:
    repo = await make_repo(db)
    row = event_row(repo.id, rating="low", heuristic_score=0.25)
    await seed(db, [row])
    event = await db.get(Event, row["id"])
    assert event is not None

    log_decision(db, event, "generate")
    await db.commit()

    decision = (await db.scalars(select(Decision).where(Decision.event_id == row["id"]))).one()
    assert (decision.action, decision.rating_at_time, decision.heuristic_at_time) == (
        "generate",
        "low",
        0.25,
    )
