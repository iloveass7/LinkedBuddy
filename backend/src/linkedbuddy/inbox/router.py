"""Inbox routes (mounted behind `require_owner`)."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.inbox import service

router = APIRouter(tags=["inbox"])

Session = Annotated[AsyncSession, Depends(get_session)]
StatusFilter = Literal["suggested", "new", "drafted", "dismissed", "posted"]


class InboxItem(BaseModel):
    id: uuid.UUID
    repo_full_name: str
    type: str
    rating: str | None
    rating_reason: str | None
    heuristic_score: float
    status: str
    created_at: datetime
    stats_summary: str


class InboxOut(BaseModel):
    items: list[InboxItem]
    next_cursor: str | None


@router.get("/inbox", response_model=InboxOut)
async def get_inbox(
    db: Session,
    status_: Annotated[StatusFilter | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> InboxOut:
    statuses = (status_,) if status_ else service.ACTIONABLE
    try:
        page = await service.list_inbox(db, statuses=statuses, cursor=cursor, limit=limit)
    except service.InvalidCursor as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid cursor") from exc
    return InboxOut(
        items=[InboxItem(**vars(row)) for row in page.items], next_cursor=page.next_cursor
    )


@router.post("/events/{event_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_event(event_id: uuid.UUID, db: Session) -> None:
    try:
        await service.dismiss(db, event_id)
    except service.EventNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found") from exc
    except service.EventAlreadyPosted as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="event already posted") from exc
