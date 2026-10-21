"""Inbox query (keyset-paginated, one statement) and the decision log."""

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import and_, case, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Decision, Event, Repo
from linkedbuddy.enums import EventStatus

DecisionActionName = Literal["generate", "ignore", "post", "discard"]

ACTIONABLE = (EventStatus.suggested, EventStatus.new, EventStatus.drafted)
_NULL_RANK = 3
# high < medium < low < NULL, so a NULL rating sorts last.
_RANK = case(
    (Event.rating == "high", 0),
    (Event.rating == "medium", 1),
    (Event.rating == "low", 2),
    else_=_NULL_RANK,
).label("rating_rank")


class InvalidCursor(ValueError):
    """The cursor is not one this service issued."""


@dataclass(frozen=True)
class InboxRow:
    id: uuid.UUID
    repo_full_name: str
    type: str
    rating: str | None
    rating_reason: str | None
    heuristic_score: float
    status: str
    created_at: datetime
    stats_summary: str


@dataclass(frozen=True)
class InboxPage:
    items: list[InboxRow]
    next_cursor: str | None


def encode_cursor(rank: int, created_at: datetime, event_id: uuid.UUID) -> str:
    raw = json.dumps([rank, created_at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[int, datetime, uuid.UUID]:
    try:
        rank, created, event_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        parsed = datetime.fromisoformat(created)
        if type(rank) is not int or not 0 <= rank <= _NULL_RANK or parsed.tzinfo is None:
            raise ValueError
        return rank, parsed, uuid.UUID(event_id)
    except (ValueError, TypeError, binascii.Error, UnicodeError, AttributeError) as exc:
        raise InvalidCursor from exc


def stats_summary(stats: dict[str, Any]) -> str:
    """One-line digest of an event's stats jsonb, e.g. `3 commits · +120/−40 · 5 files`."""
    parts: list[str] = []
    if isinstance(commits := stats.get("commits"), int):
        parts.append(f"{commits} commit{'' if commits == 1 else 's'}")
    additions, deletions = stats.get("additions"), stats.get("deletions")
    if isinstance(additions, int) and isinstance(deletions, int):
        parts.append(f"+{additions}/−{deletions}")
    if isinstance(files := stats.get("files"), int):
        parts.append(f"{files} file{'' if files == 1 else 's'}")
    if isinstance(tag := stats.get("tag"), str):
        parts.append(f"release {tag}")
    return " · ".join(parts)


async def list_inbox(
    db: AsyncSession,
    *,
    statuses: tuple[str, ...] = ACTIONABLE,
    cursor: str | None = None,
    limit: int = 50,
) -> InboxPage:
    """One joined SELECT of list columns only, ordered rating rank, created_at DESC, id DESC."""
    query = (
        select(
            Event.id,
            Repo.full_name,
            Event.type,
            Event.rating,
            Event.rating_reason,
            Event.heuristic_score,
            Event.status,
            Event.created_at,
            Event.stats,
            _RANK,
        )
        .join(Repo, Repo.id == Event.repo_id)
        .where(Event.status.in_(statuses))
        .order_by(_RANK, Event.created_at.desc(), Event.id.desc())
        .limit(limit + 1)  # one extra row tells whether another page exists
    )
    if cursor is not None:
        rank, created_at, event_id = decode_cursor(cursor)
        query = query.where(
            or_(
                _RANK > rank,
                and_(
                    _RANK == rank,
                    tuple_(Event.created_at, Event.id) < tuple_(created_at, event_id),
                ),
            )
        )
    rows = (await db.execute(query)).all()

    page = rows[:limit]
    items = [
        InboxRow(
            id=r.id,
            repo_full_name=r.full_name,
            type=r.type,
            rating=r.rating,
            rating_reason=r.rating_reason,
            heuristic_score=r.heuristic_score,
            status=r.status,
            created_at=r.created_at,
            stats_summary=stats_summary(r.stats),
        )
        for r in page
    ]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = encode_cursor(last.rating_rank, last.created_at.astimezone(UTC), last.id)
    return InboxPage(items, next_cursor)


def log_decision(db: AsyncSession, event: Event, action: DecisionActionName) -> None:
    """Record what the owner did with an event, snapshotting its rating and score.

    Does not commit; the caller commits together with its own change.
    """
    db.add(
        Decision(
            event_id=event.id,
            action=action,
            rating_at_time=event.rating,
            heuristic_at_time=event.heuristic_score,
        )
    )


class EventNotFound(Exception):
    pass


class EventAlreadyPosted(Exception):
    pass


async def dismiss(db: AsyncSession, event_id: uuid.UUID) -> None:
    """Mark an event dismissed and log it. Idempotent; a posted event cannot be dismissed."""
    event = await db.get(Event, event_id, with_for_update=True)
    if event is None:
        raise EventNotFound
    if event.status == EventStatus.dismissed:
        return
    if event.status == EventStatus.posted:
        raise EventAlreadyPosted
    event.status = EventStatus.dismissed
    log_decision(db, event, "ignore")
    await db.commit()
