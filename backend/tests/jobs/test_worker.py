import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.db.models import Job
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer, enqueue
from linkedbuddy.jobs.worker import run_worker

DEFER_UNTIL = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)


async def _echo(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True}


async def _defer(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    raise Defer(DEFER_UNTIL)


async def _deferrals(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return {"seen": ctx.deferrals}


async def _boom(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("nope")


async def _none(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return None  # type: ignore[return-value]


TEST_HANDLERS = {
    "t_echo": _echo,
    "t_defer": _defer,
    "t_deferrals": _deferrals,
    "t_boom": _boom,
    "t_none": _none,
}


# The worker's fallback poll. Every wait below stays well under it, so a job that finishes in
# time was picked up by a claim or a NOTIFY wake-up, never by the poll.
FALLBACK_POLL_S = 60.0
# One job costs ~4 s against the remote test DB (enqueue, NOTIFY, claim, run, complete and the
# test's own polling are each a few round trips), and more under full-suite load.
JOB_WAIT_S = 20.0
# The first job also waits for the worker to start (engine, LISTEN connection, one listener per
# kind).
FIRST_JOB_WAIT_S = 30.0


@pytest.fixture(scope="module")
async def worker(test_database_url: str) -> AsyncIterator[None]:
    """One worker for the module. The long fallback poll means only claims and NOTIFY wake it."""
    HANDLERS.update(TEST_HANDLERS)
    settings = Settings().model_copy(  # type: ignore[call-arg]
        update={"database_url": test_database_url, "gemini_api_key": None}
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(list(TEST_HANDLERS), 2, stop, settings=settings, poll_interval=FALLBACK_POLL_S)
    )
    yield
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    for kind in TEST_HANDLERS:
        HANDLERS.pop(kind)


async def _enqueue(maker: async_sessionmaker[AsyncSession], kind: str) -> tuple[uuid.UUID, float]:
    """(job id, loop time just before the enqueue): waits are measured from the enqueue."""
    started = asyncio.get_running_loop().time()
    async with maker() as s:
        job_id = await enqueue(s, kind, {})
        await s.commit()
    assert job_id is not None
    return job_id, started


async def _wait_for(
    maker: async_sessionmaker[AsyncSession],
    enqueued: tuple[uuid.UUID, float],
    done: Any,
    within: float = JOB_WAIT_S,
) -> Job:
    job_id, started = enqueued
    deadline = started + within
    async with maker() as s:
        while True:
            job = await s.get(Job, job_id, populate_existing=True)
            assert job is not None
            if done(job):
                return job
            assert asyncio.get_running_loop().time() < deadline, f"job stuck: {job.status}"
            await s.rollback()  # fresh snapshot on the next read
            await asyncio.sleep(0.1)


async def test_worker_runs_handler(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    first = await _enqueue(committed, "t_echo")
    job = await _wait_for(committed, first, lambda j: j.status == "done", within=FIRST_JOB_WAIT_S)
    assert job.result is not None
    assert job.result["ok"] is True and "duration_ms" in job.result
    assert job.finished_at is not None and job.locked_at is None

    # The worker is idle on LISTEN; only NOTIFY wakes it well before the fallback poll.
    second = await _enqueue(committed, "t_echo")
    await _wait_for(committed, second, lambda j: j.status == "done")


async def test_worker_handles_defer(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    enqueued = await _enqueue(committed, "t_defer")
    job = await _wait_for(committed, enqueued, lambda j: j.run_after == DEFER_UNTIL)
    assert (job.status, job.attempts, job.error) == ("queued", 0, None)
    assert job.result == {"deferrals": 1}


async def test_worker_passes_deferral_count_to_handler(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    started = asyncio.get_running_loop().time()
    async with committed() as s:  # as if deferred three times before
        job_id = await enqueue(s, "t_deferrals", {})
        await s.execute(update(Job).where(Job.id == job_id).values(result={"deferrals": 3}))
        await s.commit()
    assert job_id is not None
    job = await _wait_for(committed, (job_id, started), lambda j: j.status == "done")
    assert job.result is not None and job.result["seen"] == 3


async def test_worker_records_handler_error(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    enqueued = await _enqueue(committed, "t_boom")
    job = await _wait_for(committed, enqueued, lambda j: j.error is not None)
    assert job.error == "ValueError: nope"
    assert (job.status, job.attempts) == ("queued", 1)  # retried later with backoff


async def test_worker_rejects_non_dict_result(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    enqueued = await _enqueue(committed, "t_none")
    job = await _wait_for(committed, enqueued, lambda j: j.status != "running" and j.attempts == 1)
    assert job.status in ("queued", "failed")
    assert job.error is not None and "NoneType" in job.error
