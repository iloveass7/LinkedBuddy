"""Job `generate_draft` and the inline quick edit.

generate_draft builds (or reuses) the event's context, asks Gemini's quality model for 2-3
evidence-tagged variants, validates every evidence id against the context, lints each variant and
stores one Draft per variant. No transaction is held across the GitHub or Gemini calls, and the
event's status is restored whenever the run does not finish (there is no worker failure hook).
Prompts, draft bodies and model output are never logged.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Draft, Event, Job, Media, Repo
from linkedbuddy.drafts.context import ContextPackage, build_context
from linkedbuddy.drafts.lint import BUILTIN_BAN, ban_phrases, lint
from linkedbuddy.drafts.prompts import (
    PROMPT_VERSION,
    QUICK_EDIT_PRESETS,
    draft_system_prompt,
    draft_user_prompt,
    quick_edit_system_prompt,
    quick_edit_user_prompt,
)
from linkedbuddy.drafts.validate import (
    MERMAID_MAX_CHARS,
    DraftResult,
    Variant,
    validate_segments,
)
from linkedbuddy.enums import (
    DraftStatus,
    EventStatus,
    JobStatus,
    MediaSource,
    MediaStatus,
    MediaType,
    PrivacyMode,
    Tone,
)
from linkedbuddy.github import ingest
from linkedbuddy.github.client import GitHubRateLimited
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer, enqueue
from linkedbuddy.llm.provider import LLMProvider, LLMRateLimited, LLMUnavailable
from linkedbuddy.security.redact import RedactionBlocked
from linkedbuddy.settings.service import SettingsOut, get_cached_settings

GENERATE_DRAFT = "generate_draft"
RENDER_DIAGRAM = "render_diagram"
DRAFT_TEMPERATURE = 0.8
QUICK_EDIT_TEMPERATURE = 0.5
QUICK_EDIT_TIMEOUT_S = 30.0
# Statuses a run moves to `generating` (and back on failure).
GENERATABLE = (EventStatus.new, EventStatus.suggested, EventStatus.drafted)


def _draft_values(variant: Variant, known_ids: set[str], bans: list[str]) -> dict[str, Any]:
    """The Draft columns that come from one validated, linted variant."""
    checked = validate_segments(variant.segments, known_ids)
    report = lint(checked.body, bans)
    return {
        "angle": variant.angle,
        "body": checked.body,
        "segments": checked.segments,
        "alt_openings": list(variant.alt_openings),
        "first_comment": variant.first_comment,
        "unsupported_claims": checked.unsupported_claims,
        "specificity_count": report.specificity_count,
        "lint": report.as_dict(),
        "prompt_version": PROMPT_VERSION,
    }


def ban_list_for(owner: SettingsOut) -> list[str]:
    return ban_phrases(BUILTIN_BAN, owner.ban_list)


async def _already_generated(
    db: AsyncSession,
    payload: dict[str, Any],
    event_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    tone: Tone,
) -> bool:
    """True if the running job for this exact payload already stored its drafts.

    A worker can crash after committing the drafts but before marking the job done; the stale
    lock is then reclaimed and the job runs again. Drafts of this kind (same event, parent and
    tone, not a quick edit) created at or after the job itself mean that run already happened.
    Called directly (no job row), this is always False.
    """
    job_created = (
        select(Job.created_at)
        .where(
            Job.kind == GENERATE_DRAFT,
            Job.status == JobStatus.running,
            Job.payload == payload,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    found = await db.scalar(
        select(Draft.id)
        .where(
            Draft.event_id == event_id,
            Draft.parent_draft_id.is_not_distinct_from(parent_id),
            Draft.tone == tone,
            Draft.instructions_used == "",
            Draft.created_at >= job_created,
        )
        .limit(1)
    )
    return found is not None


async def _restore(ctx: JobContext, event_id: uuid.UUID, prior: str | None) -> None:
    """Put the prior status back, unless something else (a dismissal) changed it meanwhile.

    `prior` is None when this run never moved the event to `generating`: nothing to undo.
    """
    if prior is None:
        return
    async with ctx.sessionmaker() as db:
        await db.execute(
            update(Event)
            .where(Event.id == event_id, Event.status == EventStatus.generating)
            .values(status=prior)
        )
        await db.commit()


async def generate_draft(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: {"event_id": str, "tone": Tone | None, "parent_draft_id": str | None}."""
    event_id = uuid.UUID(str(payload["event_id"]))
    raw_parent = payload.get("parent_draft_id")
    parent_id = uuid.UUID(str(raw_parent)) if raw_parent else None

    async with ctx.sessionmaker() as db:
        prior = await db.scalar(select(Event.status).where(Event.id == event_id))
        if prior is None:
            return {"skipped": "event not found"}
        owner = await get_cached_settings(db)
        tone = Tone(payload.get("tone") or owner.default_tone)
        if await _already_generated(db, payload, event_id, parent_id, tone):
            return {"skipped": "already_generated"}
        gh = await ingest.get_github_client(db, ctx.http, ctx.settings)
        # Only an inbox status moves to `generating`. A dismissed or posted event keeps its
        # status (the drafts are still made), and a concurrent run's `generating` is left alone.
        moved = await db.execute(
            update(Event)
            .where(Event.id == event_id, Event.status.in_(GENERATABLE))
            .values(status=EventStatus.generating)
            .returning(Event.status)
        )
        restore_to = prior if moved.first() is not None else None
        await db.commit()

    try:
        async with ctx.sessionmaker() as db:
            pkg = await build_context(db, gh, event_id, PrivacyMode(owner.privacy_mode))
        bans = ban_list_for(owner)
        result = await ctx.llm.generate_json(
            tier="quality",
            system=draft_system_prompt(
                tone=tone,
                custom_instructions=owner.custom_instructions,
                voice_samples=owner.voice_samples,
                ban_list=bans,
                post_length=owner.post_length,
                emoji_policy=owner.emoji_policy,
                hashtag_count=owner.hashtag_count,
            ),
            user=draft_user_prompt(pkg.render()),
            schema=DraftResult,
            temperature=DRAFT_TEMPERATURE,
        )
        known = pkg.ids()
        rows = [_draft_values(v, known, bans) for v in result.variants]
        async with ctx.sessionmaker() as db:
            diagram_queued = await _store(
                db,
                ctx,
                event_id,
                parent_id,
                tone,
                rows,
                result.diagram_mermaid,
                result.alt_text_diagram,
            )
    except RedactionBlocked:
        await _restore(ctx, event_id, restore_to)
        return {"skipped": "privacy"}
    except LLMUnavailable:
        await _restore(ctx, event_id, restore_to)
        return {"skipped": "llm_unavailable"}
    except LLMRateLimited as exc:
        await _restore(ctx, event_id, restore_to)
        raise Defer(datetime.now(UTC) + timedelta(seconds=exc.retry_after)) from exc
    except GitHubRateLimited as exc:
        await _restore(ctx, event_id, restore_to)
        raise Defer(exc.reset_at) from exc
    except BaseException:  # also CancelledError, when the worker shuts down mid-call
        await _restore(ctx, event_id, restore_to)
        raise
    return {"drafts": len(rows), "diagram": diagram_queued}


async def _store(
    db: AsyncSession,
    ctx: JobContext,
    event_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    tone: Tone,
    rows: list[dict[str, Any]],
    mermaid: str | None,
    alt_text: str | None,
) -> bool:
    """Insert the drafts (and the diagram), then mark the event drafted. One transaction.

    The drafts are always kept: only an event still `generating` becomes `drafted`, so a dismissed
    or posted event (before or during the run) keeps its status.
    """
    drafts = [
        Draft(
            event_id=event_id,
            parent_draft_id=parent_id,
            variant_index=i,
            tone=tone,
            model=ctx.settings.gemini_model_quality,
            **row,
        )
        for i, row in enumerate(rows)
    ]
    db.add_all(drafts)
    await db.flush()
    diagram = (mermaid or "").strip()
    queued = bool(diagram) and len(diagram) <= MERMAID_MAX_CHARS
    if queued:
        media = Media(
            draft_id=drafts[0].id,
            type=MediaType.diagram,
            source=MediaSource.generated,
            status=MediaStatus.pending,
            mermaid_source=diagram,
            alt_text=alt_text,
        )
        db.add(media)
        await db.flush()
        await enqueue(
            db, RENDER_DIAGRAM, {"media_id": str(media.id)}, dedupe_key=f"diagram:{media.id}"
        )
    await db.execute(
        update(Event)
        .where(Event.id == event_id, Event.status == EventStatus.generating)
        .values(status=EventStatus.drafted)
    )
    await db.commit()
    return queued


# --- quick edit (inline, ARCHITECTURE.md 5.3) -----------------------------------------------


class DraftNotFound(Exception):
    pass


class ContextNotBuilt(Exception):
    pass


async def quick_edit(
    db: AsyncSession, llm: LLMProvider, model: str, draft_id: uuid.UUID, instruction: str
) -> Draft:
    """One quality call on the current draft; returns the new child draft (committed).

    `instruction` is a preset key or the owner's free text. Raises DraftNotFound,
    ContextNotBuilt, RedactionBlocked, TimeoutError and the LLM errors.
    """
    row = (
        await db.execute(
            select(Draft, Event.context_json, Repo.never_send_to_llm)
            .join(Event, Event.id == Draft.event_id)
            .join(Repo, Repo.id == Event.repo_id)
            .where(Draft.id == draft_id)
        )
    ).one_or_none()
    if row is None:
        raise DraftNotFound
    parent, context_json, never_send = row
    if not context_json:
        raise ContextNotBuilt
    owner = await get_cached_settings(db)
    if never_send or owner.privacy_mode == PrivacyMode.never_send:
        raise RedactionBlocked("never_send")
    known = ContextPackage.model_validate(context_json).ids()
    bans = ban_list_for(owner)
    user = quick_edit_user_prompt(
        parent.body, parent.segments, QUICK_EDIT_PRESETS.get(instruction, instruction)
    )
    await db.commit()  # no transaction stays open across the Gemini call

    edited = await asyncio.wait_for(
        llm.generate_json(
            tier="quality",
            system=quick_edit_system_prompt(bans),
            user=user,
            schema=Variant,
            temperature=QUICK_EDIT_TEMPERATURE,
        ),
        QUICK_EDIT_TIMEOUT_S,
    )
    child = Draft(
        event_id=parent.event_id,
        parent_draft_id=parent.id,
        series_id=parent.series_id,
        variant_index=parent.variant_index,
        tone=parent.tone,
        instructions_used=instruction,
        model=model,
        status=DraftStatus.draft,
        **_draft_values(edited, known, bans),
    )
    db.add(child)
    await db.commit()
    return child


HANDLERS[GENERATE_DRAFT] = generate_draft
