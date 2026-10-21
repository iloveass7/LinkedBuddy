"""Scheduler: enqueue due polls, reclaim stale jobs, unstick events, purge retention.

DATABASE.md 8, 9.

`python -m linkedbuddy.scheduler` runs `tick` every 60 s.
"""

import asyncio
import logging
import signal
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import (
    CursorResult,
    Delete,
    Interval,
    Text,
    Update,
    case,
    delete,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings, get_settings
from linkedbuddy.db.engine import make_engine, make_sessionmaker
from linkedbuddy.db.models import AuthSession, Draft, Event, Job, Repo
from linkedbuddy.enums import EventStatus, JobStatus, RepoMode
from linkedbuddy.jobs.context import RENDER_VIDEO
from linkedbuddy.jobs.queue import enqueue

log = logging.getLogger(__name__)

TICK_INTERVAL_S = 60.0
STALE_LOCK = timedelta(minutes=10)
VIDEO_STALE_MARGIN = timedelta(minutes=5)  # on top of video_timeout_s
DONE_JOB_TTL = timedelta(days=7)
FAILED_JOB_TTL = timedelta(days=30)
CONTEXT_TTL = timedelta(days=7)  # after the event's newest draft (SECURITY.md 6.9)


@dataclass
class TickReport:
    polls_enqueued: int = 0
    stale_requeued: int = 0
    stale_failed: int = 0
    sessions_deleted: int = 0
    done_jobs_deleted: int = 0
    failed_jobs_deleted: int = 0
    contexts_cleared: int = 0
    generating_reset: int = 0


async def tick(db: AsyncSession, now: datetime, *, settings: Settings | None = None) -> TickReport:
    """One scheduler pass. Does not commit; the caller commits the whole pass."""
    settings = settings or get_settings()
    poll_interval = timedelta(minutes=settings.poll_interval_minutes)
    report = TickReport()

    due = await db.scalars(
        select(Repo.id).where(
            Repo.mode != RepoMode.muted,
            or_(Repo.last_polled_at.is_(None), Repo.last_polled_at < now - poll_interval),
        )
    )
    for repo_id in due.all():
        job_id = await enqueue(
            db, "poll_repo", {"repo_id": str(repo_id)}, dedupe_key=f"poll:{repo_id}"
        )
        report.polls_enqueued += job_id is not None

    # Video renders legitimately run up to video_timeout_s. Lock ages use DB time, like claim().
    video_window = timedelta(seconds=settings.video_timeout_s) + VIDEO_STALE_MARGIN
    window = case(
        (Job.kind == RENDER_VIDEO, literal(video_window, Interval)),
        else_=literal(STALE_LOCK, Interval),
    )
    spent = Job.attempts >= Job.max_attempts
    reclaimed = await db.scalars(
        update(Job)
        .where(Job.status == JobStatus.running, Job.locked_at < func.now() - window)
        .values(
            status=case((spent, JobStatus.failed.value), else_=JobStatus.queued.value),
            error=case((spent, "stale lock: worker did not finish"), else_=Job.error),
            finished_at=case((spent, func.now()), else_=None),
            locked_at=None,
        )
        .returning(Job.status)
    )
    for status in reclaimed.all():
        if status == JobStatus.failed:
            report.stale_failed += 1
        else:
            report.stale_requeued += 1

    # An event left `generating` with no live generate_draft job (the worker died) goes back to
    # the inbox: `drafted` if it already has drafts, else `suggested`.
    live_generate = exists().where(
        Job.kind == "generate_draft",
        Job.status.in_([JobStatus.queued, JobStatus.running]),
        Job.payload["event_id"].astext == Event.id.cast(Text),
    )
    report.generating_reset = await _count(
        db,
        update(Event)
        .where(Event.status == EventStatus.generating, ~live_generate)
        .values(
            status=case(
                (exists().where(Draft.event_id == Event.id), EventStatus.drafted.value),
                else_=EventStatus.suggested.value,
            )
        ),
    )

    report.sessions_deleted = await _count(
        db, delete(AuthSession).where(AuthSession.expires_at < now)
    )
    report.done_jobs_deleted = await _count(
        db, delete(Job).where(Job.status == JobStatus.done, Job.finished_at < now - DONE_JOB_TTL)
    )
    report.failed_jobs_deleted = await _count(
        db,
        delete(Job).where(
            Job.status == JobStatus.failed,
            func.coalesce(Job.finished_at, Job.created_at) < now - FAILED_JOB_TTL,
        ),
    )
    stale_events = (
        select(Draft.event_id)
        .group_by(Draft.event_id)
        .having(func.max(Draft.created_at) < now - CONTEXT_TTL)
    )
    report.contexts_cleared = await _count(
        db,
        update(Event)
        .where(Event.context_json.is_not(None), Event.id.in_(stale_events))
        .values(context_json=None),
    )
    return report


async def _count(db: AsyncSession, stmt: Delete | Update) -> int:
    result = cast(CursorResult[Any], await db.execute(stmt))
    return result.rowcount


async def run_scheduler(stop: asyncio.Event) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    try:
        while not stop.is_set():
            try:
                async with sm() as db:
                    report = await tick(db, datetime.now(UTC), settings=settings)
                    await db.commit()
                log.info("tick %s", asdict(report))
            except Exception as exc:  # DB hiccup: try again next tick
                log.warning("tick failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_INTERVAL_S)
            except TimeoutError:
                pass
    finally:
        await engine.dispose()


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await run_scheduler(stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(_main())
