"""Media routes (mounted behind `require_owner`): screenshot upload, tray edits, diagram source
edits, and the only way a file is served (SECURITY.md 5.4)."""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Draft, Job, Media
from linkedbuddy.drafts.generate import RENDER_DIAGRAM
from linkedbuddy.drafts.router import JobAccepted, MediaOut
from linkedbuddy.drafts.validate import MERMAID_MAX_CHARS
from linkedbuddy.enums import JobStatus, MediaSource, MediaStatus, MediaType
from linkedbuddy.jobs.queue import enqueue
from linkedbuddy.media.images import MAX_UPLOAD_BYTES, reencode_upload
from linkedbuddy.media.storage import media_path, new_media_name

router = APIRouter(tags=["media"])

Session = Annotated[AsyncSession, Depends(get_session)]
MAX_MEDIA_PER_DRAFT = 20
MAX_POSITION = 20
SERVABLE_MIMES = frozenset({"image/png", "image/jpeg", "video/mp4"})
_MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg"}


class MediaPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alt_text: Annotated[str | None, Field(max_length=1000)] = None
    position: Annotated[int | None, Field(ge=0, le=MAX_POSITION)] = None
    included: bool | None = None


class MermaidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MERMAID_MAX_CHARS)
    ]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="media not found")


@router.post(
    "/drafts/{draft_id}/media", response_model=MediaOut, status_code=status.HTTP_201_CREATED
)
async def upload_media(
    draft_id: uuid.UUID, request: Request, db: Session, file: Annotated[UploadFile, File()]
) -> MediaOut:
    if await db.scalar(select(Draft.id).where(Draft.id == draft_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")
    count, last = (
        await db.execute(
            select(func.count(Media.id), func.max(Media.position)).where(Media.draft_id == draft_id)
        )
    ).one()
    if count >= MAX_MEDIA_PER_DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="media tray is full")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="file is too large")
    try:
        clean, ext = await reencode_upload(data)
    except ValueError as exc:  # messages are fixed strings from media.images
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    root = request.app.state.settings.media_dir
    name = new_media_name(ext)
    path = media_path(name, root)
    await asyncio.to_thread(_write, path, clean)
    media = Media(
        draft_id=draft_id,
        type=MediaType.screenshot,
        source=MediaSource.uploaded,
        file_path=name,
        mime=_MIME_BY_EXT[ext],
        status=MediaStatus.ready,
        position=0 if last is None else last + 1,
    )
    try:
        db.add(media)
        await db.flush()
        out = MediaOut.model_validate(media)
        await db.commit()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return out


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@router.patch("/media/{media_id}", response_model=MediaOut)
async def patch_media(media_id: uuid.UUID, body: MediaPatch, db: Session) -> MediaOut:
    media = await db.get(Media, media_id)
    if media is None:
        raise _not_found()
    if "alt_text" in body.model_fields_set:
        media.alt_text = body.alt_text
    if body.position is not None:
        media.position = body.position
    if body.included is not None:
        media.included = body.included
    out = MediaOut.model_validate(media)
    await db.commit()
    return out


@router.put(
    "/media/{media_id}/mermaid", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def put_mermaid(media_id: uuid.UUID, body: MermaidIn, db: Session) -> JobAccepted:
    media = await db.get(Media, media_id)
    if media is None:
        raise _not_found()
    if media.type != MediaType.diagram:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="media is not a diagram")
    media.mermaid_source = body.source
    media.status = MediaStatus.pending
    media.meta = {k: v for k, v in media.meta.items() if k != "reason"}
    key = f"diagram:{media_id}"
    payload = {"media_id": str(media_id)}
    job_id = None
    for _ in range(2):  # a live job may finish between the insert and the lookup
        job_id = await enqueue(db, RENDER_DIAGRAM, payload, dedupe_key=key)
        if job_id is None:
            # A render is already queued or running: it re-checks the source hash when it
            # finishes and re-runs itself if this edit arrived mid-render.
            job_id = await db.scalar(
                select(Job.id)
                .where(Job.dedupe_key == key, Job.status.in_([JobStatus.queued, JobStatus.running]))
                .order_by(Job.created_at.desc())
                .limit(1)
            )
        if job_id is not None:
            break
    if job_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="job busy, retry")
    await db.commit()
    return JobAccepted(job_id=job_id)


@router.get("/media/{media_id}/file")
async def get_media_file(media_id: uuid.UUID, request: Request, db: Session) -> FileResponse:
    media = await db.get(Media, media_id)
    if (
        media is None
        or media.status != MediaStatus.ready
        or not media.file_path
        or media.mime not in SERVABLE_MIMES
    ):
        raise _not_found()
    try:
        path = media_path(media.file_path, request.app.state.settings.media_dir)
    except ValueError:
        raise _not_found() from None
    if not path.is_file():
        raise _not_found()
    return FileResponse(
        path,
        media_type=media.mime,
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )
