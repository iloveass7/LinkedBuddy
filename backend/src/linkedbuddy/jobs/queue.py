"""Queue operations on the `jobs` table (DATABASE.md 8).

None of these functions commit: the caller's transaction does, so an enqueue is atomic with the
caller's own writes and a claim's row lock is held only until the caller commits.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Job
from linkedbuddy.enums import JobStatus

BACKOFF_BASE_S = 30

_CLAIM = text(
    "UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 "
    "WHERE id = (SELECT id FROM jobs WHERE status='queued' AND kind = ANY(:kinds) "
    "AND run_after <= now() ORDER BY run_after LIMIT 1 FOR UPDATE SKIP LOCKED) "
    "RETURNING *"
)


class Defer(Exception):
    """Raised by a handler to re-run the job at `until` without using up an attempt."""

    def __init__(self, until: datetime) -> None:
        super().__init__(f"deferred until {until.isoformat()}")
        self.until = until


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
) -> uuid.UUID | None:
    """Queue a job and NOTIFY `jobs_<kind>`. Does not commit: the caller's commit publishes both.

    Returns None when a queued or running job already holds `dedupe_key`.
    """
    values: dict[str, Any] = {"kind": kind, "payload": payload, "dedupe_key": dedupe_key}
    if run_after is not None:
        values["run_after"] = run_after
    stmt = (
        pg_insert(Job)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=text("status IN ('queued','running')"),
        )
        .returning(Job.id)
    )
    job_id: uuid.UUID | None = await db.scalar(stmt)
    if job_id is not None:
        # Delivered only when the transaction commits, so the worker never wakes too early.
        await db.execute(select(func.pg_notify(f"jobs_{kind}", "")))
    return job_id


async def claim(db: AsyncSession, kinds: list[str]) -> Job | None:
    """Take the oldest runnable job of `kinds` (one statement, SKIP LOCKED). Commit right after."""
    stmt = (
        select(Job)
        .from_statement(_CLAIM.bindparams(kinds=kinds))
        .execution_options(populate_existing=True)
    )
    job: Job | None = await db.scalar(stmt)
    return job


async def complete(db: AsyncSession, job_id: uuid.UUID, result: dict[str, Any]) -> None:
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.done,
            result=result,
            error=None,
            locked_at=None,
            finished_at=func.now(),
        )
    )


async def fail(db: AsyncSession, job_id: uuid.UUID, error: str) -> None:
    """Record a failed attempt: re-queue with backoff 30 s * 2**attempts, or fail for good."""
    spent = Job.attempts >= Job.max_attempts
    backoff = func.make_interval(0, 0, 0, 0, 0, 0, BACKOFF_BASE_S * func.power(2, Job.attempts))
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=case((spent, JobStatus.failed.value), else_=JobStatus.queued.value),
            run_after=case((spent, Job.run_after), else_=func.now() + backoff),
            finished_at=case((spent, func.now()), else_=None),
            error=error,
            locked_at=None,
        )
    )


async def defer(db: AsyncSession, job_id: uuid.UUID, until: datetime) -> None:
    """Re-queue at `until` and give back the attempt the claim took (rate limits, not errors)."""
    await db.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            status=JobStatus.queued,
            attempts=func.greatest(Job.attempts - 1, 0),
            run_after=until,
            locked_at=None,
        )
    )
