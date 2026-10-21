"""Copy-paste publishing, post history and manual metrics (SECURITY.md 9, DATABASE.md 7).

Nothing here talks to LinkedIn: the owner pastes the approved draft by hand and then records it.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, true, tuple_, update
from sqlalchemy import select as sa_select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import literal

from linkedbuddy.db.models import Draft, Event, Post, Repo
from linkedbuddy.enums import DraftStatus, EventStatus, PublishMethod
from linkedbuddy.inbox.service import log_decision


class DraftNotFound(Exception):
    pass


class DraftNotApproved(Exception):
    """Only an `approved` draft can be published (this also covers an already posted one)."""


class PostNotFound(Exception):
    pass


class RepoNotFound(Exception):
    pass


class InvalidCursor(ValueError):
    """The cursor is not one this service issued."""


async def publish(db: AsyncSession, draft_id: uuid.UUID, final_text: str) -> Post:
    """Record a manual publish in one transaction. The caller has validated `final_text`.

    Locks the event, then the draft (always in that order), so two publishes of one draft, or of
    two sibling drafts, serialise and the loser sees `posted`/`discarded` and gets a 409.
    """
    event_id = await db.scalar(sa_select(Draft.event_id).where(Draft.id == draft_id))
    if event_id is None:
        raise DraftNotFound
    event = await db.scalar(
        sa_select(Event)
        .where(Event.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    draft = await db.scalar(
        sa_select(Draft)
        .where(Draft.id == draft_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None or draft is None:  # deleted while we waited for the lock
        raise DraftNotFound
    if draft.status != DraftStatus.approved:
        raise DraftNotApproved

    post = Post(
        draft_id=draft.id,
        publish_method=PublishMethod.manual,
        final_text=final_text,
        covered_base_sha=event.base_sha,
        covered_head_sha=event.head_sha,
    )
    db.add(post)
    draft.status = DraftStatus.posted
    event.status = EventStatus.posted
    # Quick-edit children are siblings too: any other live draft of this event is dropped.
    await db.execute(
        update(Draft)
        .where(
            Draft.event_id == event.id,
            Draft.id != draft.id,
            Draft.status.in_([DraftStatus.draft, DraftStatus.approved]),
        )
        .values(status=DraftStatus.discarded)
        .execution_options(synchronize_session=False)
    )
    log_decision(db, event, "post")
    await db.flush()  # fills posted_at / metrics from their server defaults
    await db.refresh(post)
    await db.commit()
    return post


async def update_metrics(db: AsyncSession, post_id: uuid.UUID, fields: dict[str, int]) -> Post:
    """Merge only the given counters into `metrics`; atomic in SQL (JSONB concatenation)."""
    post = await db.scalar(
        update(Post)
        .where(Post.id == post_id)
        .values(metrics=Post.metrics.op("||")(literal(fields, JSONB)))
        .returning(Post)
        .execution_options(populate_existing=True)
    )
    if post is None:
        raise PostNotFound
    await db.commit()
    return post


@dataclass(frozen=True)
class PostPage:
    items: Sequence[Row[Any]]
    next_cursor: str | None


def encode_cursor(posted_at: datetime, post_id: uuid.UUID) -> str:
    raw = json.dumps([posted_at.astimezone(UTC).isoformat(), str(post_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        posted, post_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        parsed = datetime.fromisoformat(posted)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed, uuid.UUID(post_id)
    except (ValueError, TypeError, binascii.Error, UnicodeError, AttributeError) as exc:
        raise InvalidCursor from exc


async def list_posts(
    db: AsyncSession, repo_id: uuid.UUID, *, cursor: str | None = None, limit: int = 20
) -> PostPage:
    """One statement: the repo row LEFT JOINed to its newest page of posts.

    The repo row is what tells "no posts yet" (empty page) from "no such repo" (RepoNotFound),
    so both cases cost one query. Keyset order is (posted_at, id) DESC.
    """
    page = (
        sa_select(
            Post.id,
            Post.draft_id,
            Post.posted_at,
            Post.publish_method,
            Post.final_text,
            Post.covered_base_sha,
            Post.covered_head_sha,
            Post.metrics,
        )
        .join(Draft, Draft.id == Post.draft_id)
        .join(Event, Event.id == Draft.event_id)
        .where(Event.repo_id == repo_id)
        .order_by(Post.posted_at.desc(), Post.id.desc())
        .limit(limit + 1)  # one extra row tells whether another page exists
    )
    if cursor is not None:
        posted_at, post_id = decode_cursor(cursor)
        page = page.where(tuple_(Post.posted_at, Post.id) < tuple_(posted_at, post_id))
    sub = page.subquery("page")
    rows = (
        await db.execute(
            sa_select(sub)
            .select_from(Repo)
            .outerjoin(sub, true())
            .where(Repo.id == repo_id)
            .order_by(sub.c.posted_at.desc().nulls_last(), sub.c.id.desc().nulls_last())
        )
    ).all()
    if not rows:
        raise RepoNotFound
    posts = [r for r in rows if r.id is not None]  # the single NULL row means "no posts"
    items = posts[:limit]
    next_cursor = None
    if len(posts) > limit:
        last = items[-1]
        next_cursor = encode_cursor(last.posted_at, last.id)
    return PostPage(items, next_cursor)
