"""Draft routes (mounted behind `require_owner`).

Generation and regeneration go through the `generate_draft` job (202). Only the quick edit calls
Gemini inline, once, with a 30 s timeout (ARCHITECTURE.md 5.3).
"""

import math
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Draft, Event, Job, Media
from linkedbuddy.drafts import generate
from linkedbuddy.drafts.lint import LINKEDIN_MAX_CHARS, lint
from linkedbuddy.enums import DraftStatus, EventStatus, JobStatus, Tone
from linkedbuddy.inbox.service import log_decision
from linkedbuddy.jobs.queue import enqueue
from linkedbuddy.llm.provider import LLMInvalidOutput, LLMRateLimited, LLMUnavailable
from linkedbuddy.security.redact import RedactionBlocked
from linkedbuddy.settings.service import get_cached_settings

router = APIRouter(tags=["drafts"])

Session = Annotated[AsyncSession, Depends(get_session)]
MAX_LISTED_DRAFTS = 100


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tone: Tone | None = None


class RegenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tone: Tone


class DraftPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: Annotated[str | None, Field(max_length=LINKEDIN_MAX_CHARS)] = None
    status: Literal["approved", "discarded", "draft"] | None = None


QuickEditPreset = Literal["shorter", "more_casual", "less_buzzwordy"]
FreeInstruction = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class QuickEditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: QuickEditPreset | FreeInstruction


class JobAccepted(BaseModel):
    job_id: uuid.UUID


class MediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    source: str
    status: str
    alt_text: str | None
    position: int
    included: bool
    mermaid_source: str | None


class DraftOut(BaseModel):
    id: uuid.UUID
    event_id: uuid.UUID
    parent_draft_id: uuid.UUID | None
    variant_index: int
    angle: str
    tone: str
    body: str
    segments: list[dict[str, Any]]
    alt_openings: list[str]
    first_comment: str | None
    lint: dict[str, Any]
    unsupported_claims: int
    specificity_count: int
    status: str
    media: list[MediaOut]


def draft_out(draft: Draft, media: list[Media]) -> DraftOut:
    return DraftOut(
        id=draft.id,
        event_id=draft.event_id,
        parent_draft_id=draft.parent_draft_id,
        variant_index=draft.variant_index,
        angle=draft.angle,
        tone=draft.tone,
        body=draft.body,
        segments=draft.segments,
        alt_openings=draft.alt_openings,
        first_comment=draft.first_comment,
        lint=draft.lint,
        unsupported_claims=draft.unsupported_claims,
        specificity_count=draft.specificity_count,
        status=draft.status,
        media=[MediaOut.model_validate(m) for m in sorted(media, key=lambda m: m.position)],
    )


def _with_media(draft_id: uuid.UUID) -> Any:
    return select(Draft).options(selectinload(Draft.media)).where(Draft.id == draft_id)


def _draft_not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found")


async def _enqueue_generate(
    db: AsyncSession, payload: dict[str, Any], key: str
) -> tuple[uuid.UUID, bool]:
    """(job id, newly queued). A live job holding `key` is returned instead of a new one."""
    for _ in range(2):  # the live job may finish between the insert and the lookup
        job_id = await enqueue(db, generate.GENERATE_DRAFT, payload, dedupe_key=key)
        if job_id is not None:
            return job_id, True
        existing = await db.scalar(
            select(Job.id)
            .where(Job.dedupe_key == key, Job.status.in_([JobStatus.queued, JobStatus.running]))
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            return existing, False
    raise HTTPException(status.HTTP_409_CONFLICT, detail="job busy, retry")


@router.post(
    "/events/{event_id}/generate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_for_event(
    event_id: uuid.UUID, db: Session, body: GenerateIn | None = None
) -> JobAccepted:
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
    if event.status == EventStatus.dismissed:
        event.status = EventStatus.suggested  # the owner asked for drafts: back to the inbox
    tone = body.tone if body else None
    payload = {"event_id": str(event_id), "tone": tone, "parent_draft_id": None}
    job_id, queued = await _enqueue_generate(db, payload, f"generate:{event_id}")
    if queued:
        log_decision(db, event, "generate")
    await db.commit()
    return JobAccepted(job_id=job_id)


@router.get("/events/{event_id}/drafts", response_model=list[DraftOut])
async def list_drafts(event_id: uuid.UUID, db: Session) -> list[DraftOut]:
    drafts = (
        await db.scalars(
            select(Draft)
            .options(selectinload(Draft.media))
            .where(Draft.event_id == event_id)
            .order_by(Draft.created_at.desc(), Draft.variant_index)
            .limit(MAX_LISTED_DRAFTS)
        )
    ).all()
    if not drafts and await db.scalar(select(Event.id).where(Event.id == event_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
    return [draft_out(d, d.media) for d in drafts]


@router.get("/drafts/{draft_id}", response_model=DraftOut)
async def get_draft(draft_id: uuid.UUID, db: Session) -> DraftOut:
    draft = await db.scalar(_with_media(draft_id))
    if draft is None:
        raise _draft_not_found()
    return draft_out(draft, draft.media)


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
async def patch_draft(draft_id: uuid.UUID, body: DraftPatch, db: Session) -> DraftOut:
    draft = await db.scalar(_with_media(draft_id))
    if draft is None:
        raise _draft_not_found()
    if draft.status == DraftStatus.posted:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="draft already posted")
    if body.body is not None and body.body != draft.body:
        owner = await get_cached_settings(db)
        report = lint(body.body, generate.ban_list_for(owner))
        draft.body = body.body
        draft.lint = report.as_dict()
        draft.specificity_count = report.specificity_count
    if body.status is not None:
        draft.status = body.status
    out = draft_out(draft, draft.media)
    await db.commit()
    return out


@router.post(
    "/drafts/{draft_id}/regenerate",
    response_model=JobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_draft(draft_id: uuid.UUID, body: RegenerateIn, db: Session) -> JobAccepted:
    event_id = await db.scalar(select(Draft.event_id).where(Draft.id == draft_id))
    if event_id is None:
        raise _draft_not_found()
    payload = {"event_id": str(event_id), "tone": body.tone, "parent_draft_id": str(draft_id)}
    job_id, _ = await _enqueue_generate(db, payload, f"regenerate:{draft_id}:{body.tone}")
    await db.commit()
    return JobAccepted(job_id=job_id)


@router.post(
    "/drafts/{draft_id}/quick-edit",
    response_model=DraftOut,
    status_code=status.HTTP_201_CREATED,
    responses={503: {"description": "AI rate limited (Retry-After) or not configured"}},
)
async def quick_edit_draft(
    draft_id: uuid.UUID, body: QuickEditIn, request: Request, db: Session
) -> DraftOut:
    try:
        child = await generate.quick_edit(
            db,
            request.app.state.llm,
            request.app.state.settings.gemini_model_quality,
            draft_id,
            body.instruction,
        )
    except generate.DraftNotFound as exc:
        raise _draft_not_found() from exc
    except generate.ContextNotBuilt as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="context not built") from exc
    except RedactionBlocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not sent to ai (privacy)") from exc
    except TimeoutError as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="ai timed out") from exc
    except LLMRateLimited as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ai rate limited",
            headers={"Retry-After": str(max(1, math.ceil(exc.retry_after)))},
        ) from exc
    except LLMUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="ai not configured"
        ) from exc
    except LLMInvalidOutput as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="ai returned invalid output"
        ) from exc
    return draft_out(child, [])
