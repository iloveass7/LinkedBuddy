"""`POST /api/drafts/{id}/video`: the only way a video render is ever requested."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Draft, Media
from linkedbuddy.enums import MediaSource, MediaStatus, MediaType
from linkedbuddy.jobs.context import RENDER_VIDEO
from linkedbuddy.jobs.queue import enqueue
from linkedbuddy.media.router import MAX_MEDIA_PER_DRAFT, _live_job

router = APIRouter(tags=["video"])

Session = Annotated[AsyncSession, Depends(get_session)]


class VideoAccepted(BaseModel):
    job_id: uuid.UUID
    media_id: uuid.UUID


@router.post(
    "/drafts/{draft_id}/video", response_model=VideoAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def request_video(draft_id: uuid.UUID, request: Request, db: Session) -> VideoAccepted:
    if not request.app.state.settings.brag_command:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="video not configured")
    if await db.scalar(select(Draft.id).where(Draft.id == draft_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
    key = f"video:{draft_id}"
    for _ in range(2):  # a live job may finish between the insert and the lookup
        live = await _live_job(db, key)
        if live is not None:
            return VideoAccepted(job_id=live.id, media_id=uuid.UUID(str(live.payload["media_id"])))
        count, last = (
            await db.execute(
                select(func.count(Media.id), func.max(Media.position)).where(
                    Media.draft_id == draft_id
                )
            )
        ).one()
        if count >= MAX_MEDIA_PER_DRAFT:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="media tray is full")
        media_id = uuid.uuid4()
        payload = {"draft_id": str(draft_id), "media_id": str(media_id)}
        job_id = await enqueue(db, RENDER_VIDEO, payload, dedupe_key=key)
        if job_id is None:
            continue
        db.add(
            Media(
                id=media_id,
                draft_id=draft_id,
                type=MediaType.video,
                source=MediaSource.generated,
                status=MediaStatus.pending,
                position=0 if last is None else last + 1,
            )
        )
        await db.commit()
        return VideoAccepted(job_id=job_id, media_id=media_id)
    raise HTTPException(status.HTTP_409_CONFLICT, detail="job busy, retry")
