"""Queue operations on the `jobs` table (DATABASE.md 8).

None of these functions commit: the caller's transaction does, so an enqueue is atomic with the
caller's own writes and a claim's row lock is held only until the caller commits.

`defer` counts deferrals in `jobs.result` (`{"deferrals": n}`) while the job is not done, so a
handler can back off further each time (`llm_backoff`); `complete` replaces it with the result.

`complete`, `fail` and `defer` take the `attempts` value of the claim they report on and only
touch a job that is still `running` under that claim. They return False otherwise: the run was
reclaimed as stale (and maybe claimed again), so its late outcome must not overwrite the newer one.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Integer, Update, case, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Job
from linkedbuddy.enums import JobStatus

BACKOFF_BASE_S = 30
# Gemini 429s (ARCHITECTURE.md 6): exponential from 10 s, at most 10 min.
LLM_BACKOFF_START_S = 10
LLM_BACKOFF_MAX_S = 600

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


def llm_backoff(retry_after: float, deferrals: int) -> timedelta:
    """Wait before retrying an LLM 429: 10 s * 2**deferrals capped at 10 min, but never less than
    the provider's own `retry_after`."""
    exponent = min(max(deferrals, 0), 16)  # 10 * 2**16 is far past the cap already
    step = min(LLM_BACKOFF_START_S * 2**exponent, LLM_BACKOFF_MAX_S)
    return timedelta(seconds=max(retry_after, step))


def deferral_count(job: Job) -> int:
    """How many times `job` was deferred (0 for a fresh or completed job)."""
    count = (job.result or {}).get("deferrals", 0)
    return count if isinstance(count, int) else 0


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


def _of_claim(job_id: uuid.UUID, attempts: int) -> Update:
    return update(Job).where(
        Job.id == job_id, Job.status == JobStatus.running, Job.attempts == attempts
    )


async def _applied(db: AsyncSession, stmt: Update) -> bool:
    result = cast(CursorResult[Any], await db.execute(stmt))
    return result.rowcount == 1


async def complete(
    db: AsyncSession, job_id: uuid.UUID, result: dict[str, Any], *, attempts: int
) -> bool:
    return await _applied(
        db,
        _of_claim(job_id, attempts).values(
            status=JobStatus.done,
            result=result,
            error=None,
            locked_at=None,
            finished_at=func.now(),
        ),
    )


async def fail(db: AsyncSession, job_id: uuid.UUID, error: str, *, attempts: int) -> bool:
    """Record a failed attempt: re-queue with backoff 30 s * 2**attempts, or fail for good."""
    spent = Job.attempts >= Job.max_attempts
    backoff = func.make_interval(0, 0, 0, 0, 0, 0, BACKOFF_BASE_S * func.power(2, Job.attempts))
    return await _applied(
        db,
        _of_claim(job_id, attempts).values(
            status=case((spent, JobStatus.failed.value), else_=JobStatus.queued.value),
            run_after=case((spent, Job.run_after), else_=func.now() + backoff),
            finished_at=case((spent, func.now()), else_=None),
            error=error,
            locked_at=None,
        ),
    )


async def defer(db: AsyncSession, job_id: uuid.UUID, until: datetime, *, attempts: int) -> bool:
    """Re-queue at `until`, give back the attempt the claim took (rate limits, not errors) and
    count the deferral."""
    deferrals = func.coalesce(Job.result["deferrals"].as_integer(), 0) + 1
    return await _applied(
        db,
        _of_claim(job_id, attempts).values(
            status=JobStatus.queued,
            attempts=func.greatest(Job.attempts - 1, 0),
            run_after=until,
            locked_at=None,
            result=func.jsonb_build_object("deferrals", deferrals.cast(Integer)),
        ),
    )
