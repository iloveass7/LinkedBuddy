"""Job `rate_event`: ask Gemini's fast model whether an event is worth a LinkedIn post.

Runs only for events that passed the free heuristic gate. The DB is read in short sessions, the
GitHub and Gemini calls happen with no transaction held by this handler, and the result is
written in one small transaction. Prompts, summaries and model output are never logged.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from linkedbuddy.db.models import Event, SettingsRow
from linkedbuddy.drafts.context import build_context, build_rating_summary
from linkedbuddy.drafts.prompts import rating_system_prompt
from linkedbuddy.enums import PrivacyMode, Rating
from linkedbuddy.github import ingest
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer
from linkedbuddy.llm.provider import LLMRateLimited, LLMUnavailable
from linkedbuddy.security.redact import RedactionBlocked

RATING_TEMPERATURE = 0.1
REASON_PRIVACY = "Not sent to AI (privacy)"
REASON_NOT_CONFIGURED = "AI not configured"

Score = Annotated[int, Field(ge=1, le=5)]


class RatingScores(BaseModel):
    visible_outcome: Score
    demonstrability: Score
    novelty: Score
    completeness: Score
    freshness: Score
    goal_fit: Score


class RatingResult(BaseModel):
    # `reason` first: the model writes its reasoning before it commits to scores.
    reason: str = Field(max_length=300)
    scores: RatingScores
    rating: Rating
    suggested_angle: str = Field(max_length=300)
    needs_user_assets: list[str] = Field(max_length=5)


async def rate_event(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Rate one event high/medium/low with a reason. Idempotent: a rated event is left alone."""
    event_id = uuid.UUID(str(payload["event_id"]))
    async with ctx.sessionmaker() as db:
        rated = (await db.execute(select(Event.rating).where(Event.id == event_id))).one_or_none()
        if rated is None:
            return {"skipped": "event not found"}
        if rated.rating is not None:
            return {"skipped": "already_rated"}
        owner = (
            await db.execute(
                select(SettingsRow.goals_text, SettingsRow.privacy_mode).where(SettingsRow.id == 1)
            )
        ).one_or_none()
        goals = owner.goals_text if owner else ""
        privacy_mode = (
            PrivacyMode(owner.privacy_mode) if owner else PrivacyMode.metadata_plus_readme
        )
        gh = await ingest.get_github_client(db, ctx.http, ctx.settings)

    try:
        async with ctx.sessionmaker() as db:
            pkg = await build_context(db, gh, event_id, privacy_mode)
        summary = await build_rating_summary(pkg, goals)
        result = await ctx.llm.generate_json(
            tier="fast",
            system=rating_system_prompt(goals),
            user=summary,
            schema=RatingResult,
            temperature=RATING_TEMPERATURE,
        )
    except RedactionBlocked:
        await _store(ctx, event_id, rating_reason=REASON_PRIVACY)
        return {"skipped": "privacy"}
    except LLMUnavailable:
        await _store(ctx, event_id, rating_reason=REASON_NOT_CONFIGURED)
        return {"skipped": "llm_unavailable"}
    except LLMRateLimited as exc:
        raise Defer(datetime.now(UTC) + timedelta(seconds=exc.retry_after)) from exc

    await _store(
        ctx,
        event_id,
        rating=result.rating.value,
        rating_reason=result.reason,
        rating_json=result.model_dump(mode="json"),
    )
    return {"rating": result.rating.value}


async def _store(ctx: JobContext, event_id: uuid.UUID, **values: Any) -> None:
    """Write the outcome, but never over a rating that another run stored in the meantime."""
    async with ctx.sessionmaker() as db:
        await db.execute(
            update(Event).where(Event.id == event_id, Event.rating.is_(None)).values(**values)
        )
        await db.commit()


# `ingest` imports jobs.handlers, which imports this module: use the module (not its names) so a
# partially initialised `ingest` is fine; its attributes are only read when the job runs.
HANDLERS["rate_event"] = rate_event
