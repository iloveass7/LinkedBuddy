"""Job `render_video`: Gemini writes a scene script for a draft, the renderer turns it into an MP4.

Opt-in only: the sole producer is `POST /api/drafts/{id}/video`. The DB is used in short sessions
(never held across the Gemini call or the render). The Media row is `pending` from the request on
and ends `ready` or `failed`, so it can never stay `pending` once the job is out of attempts
(ARCHITECTURE.md 7.2). Prompts, scripts and model output are never logged.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Draft, Event, Media, Repo
from linkedbuddy.drafts.prompts import VIDEO_SCRIPT_SYSTEM_PROMPT, video_script_user_prompt
from linkedbuddy.enums import MediaStatus, MediaType, PrivacyMode
from linkedbuddy.jobs.context import RENDER_VIDEO, JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer
from linkedbuddy.llm.provider import LLMRateLimited, LLMUnavailable
from linkedbuddy.media.storage import media_path, new_media_name
from linkedbuddy.settings.service import get_cached_settings
from linkedbuddy.video.brag import BragRenderer
from linkedbuddy.video.renderer import RenderFailed, VideoScript

SCRIPT_TEMPERATURE = 0.6
REFERENCEABLE = (MediaType.diagram, MediaType.screenshot)
REASON_PRIVACY = "privacy"
REASON_NOT_CONFIGURED = "ai not configured"
REASON_NO_RENDERER = "video not configured"
REASON_ERROR = "render error"


async def _eligible(db: AsyncSession, draft_id: uuid.UUID) -> dict[uuid.UUID, Media]:
    """Media a scene may show: on this draft, included, ready, a diagram or a screenshot."""
    rows = await db.scalars(
        select(Media)
        .where(
            Media.draft_id == draft_id,
            Media.included.is_(True),
            Media.status == MediaStatus.ready,
            Media.type.in_(REFERENCEABLE),
            Media.file_path.is_not(None),
        )
        .order_by(Media.position, Media.id)
    )
    return {m.id: m for m in rows}


def drop_unknown_media(script: VideoScript, allowed: set[uuid.UUID]) -> VideoScript:
    """Scenes that name media outside `allowed` lose the reference; the scene itself stays."""
    scenes = [
        s.model_copy(update={"media_id": None}) if s.media_id not in allowed else s
        for s in script.scenes
    ]
    return script.model_copy(update={"scenes": scenes})


def _remove(name: str, root: Path) -> None:
    try:
        media_path(name, root).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


async def _finish(
    ctx: JobContext, media_id: uuid.UUID, *, name: str | None = None, reason: str | None = None
) -> bool:
    """Record the outcome on a media row that is not `ready`. False if the row is gone (the new
    file, if any, is then not referenced by anything and the caller removes it)."""
    async with ctx.sessionmaker() as db:
        media = await db.scalar(select(Media).where(Media.id == media_id).with_for_update())
        if media is None:
            return False
        meta = {k: v for k, v in media.meta.items() if k != "reason"}
        if name:
            media.file_path, media.mime, media.status = name, "video/mp4", MediaStatus.ready
        else:
            media.status = MediaStatus.failed
            meta["reason"] = reason
        media.meta = meta
        await db.commit()
        return True


async def render_video(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: {"draft_id": str, "media_id": str}. Idempotent: a ready video is left alone."""
    draft_id = uuid.UUID(str(payload["draft_id"]))
    media_id = uuid.UUID(str(payload["media_id"]))
    root = ctx.settings.media_dir
    async with ctx.sessionmaker() as db:
        row = (
            await db.execute(
                select(Media, Draft.body, Repo.never_send_to_llm)
                .join(Draft, Draft.id == Media.draft_id)
                .join(Event, Event.id == Draft.event_id)
                .join(Repo, Repo.id == Event.repo_id)
                .where(Media.id == media_id, Media.draft_id == draft_id)
            )
        ).one_or_none()
        if row is None:
            return {"skipped": "media not found"}
        media, body, never_send = row
        if media.type != MediaType.video:
            return {"skipped": "not a video"}
        if media.status == MediaStatus.ready and media.file_path:
            return {"skipped": "already_rendered"}
        owner = await get_cached_settings(db)
        offered = await _eligible(db, draft_id)
        listing = [(str(m.id), str(m.type), m.alt_text) for m in offered.values()]

    if never_send or owner.privacy_mode == PrivacyMode.never_send:
        await _finish(ctx, media_id, reason=REASON_PRIVACY)
        return {"skipped": REASON_PRIVACY}
    command = ctx.settings.brag_command
    if not command:
        await _finish(ctx, media_id, reason=REASON_NO_RENDERER)
        return {"skipped": REASON_NO_RENDERER}

    new_name: str | None = None
    try:
        try:
            script = await ctx.llm.generate_json(
                tier="quality",
                system=VIDEO_SCRIPT_SYSTEM_PROMPT,
                user=video_script_user_prompt(body, listing),
                schema=VideoScript,
                temperature=SCRIPT_TEMPERATURE,
            )
        except LLMUnavailable:
            await _finish(ctx, media_id, reason=REASON_NOT_CONFIGURED)
            return {"skipped": REASON_NOT_CONFIGURED}
        except LLMRateLimited as exc:
            raise Defer(datetime.now(UTC) + timedelta(seconds=exc.retry_after)) from exc

        async with ctx.sessionmaker() as db:  # fresh: the owner may have changed the tray
            current = await _eligible(db, draft_id)
        script = drop_unknown_media(script, set(current))
        assets: dict[uuid.UUID, Path] = {}
        for ref in {s.media_id for s in script.scenes if s.media_id}:
            path = media_path(str(current[ref].file_path), root)
            if path.is_file():
                assets[ref] = path
        script = drop_unknown_media(script, set(assets))

        new_name = new_media_name("mp4")
        root.mkdir(parents=True, exist_ok=True)
        try:
            await BragRenderer(command, ctx.settings.video_timeout_s).render(
                script, assets, media_path(new_name, root)
            )
        except RenderFailed as exc:
            new_name = None
            await _finish(ctx, media_id, reason=str(exc))
            return {"status": "failed", "reason": str(exc)}
        if not await _finish(ctx, media_id, name=new_name):
            _remove(new_name, root)
            return {"skipped": "media not found"}
    except BaseException as exc:
        if new_name:
            _remove(new_name, root)
        if isinstance(exc, Exception) and not isinstance(exc, Defer):
            await _mark_failed(ctx, media_id)
        raise
    return {"status": "ready"}


async def _mark_failed(ctx: JobContext, media_id: uuid.UUID) -> None:
    """Best effort: if the database is the problem this fails too, and the retry handles it."""
    try:
        async with ctx.sessionmaker() as db:
            row = await db.get(Media, media_id)
            if row is not None and row.status != MediaStatus.ready:
                row.status = MediaStatus.failed
                row.meta = {**row.meta, "reason": REASON_ERROR}
                await db.commit()
    except Exception:  # noqa: S110  # nosec B110 - the original error is what the worker records
        pass


HANDLERS[RENDER_VIDEO] = render_video
