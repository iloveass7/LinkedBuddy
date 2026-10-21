import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
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


async def _boom(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    raise ValueError("nope")


TEST_HANDLERS = {"t_echo": _echo, "t_defer": _defer, "t_boom": _boom}


@pytest.fixture(scope="module")
async def worker(test_database_url: str) -> AsyncIterator[None]:
    """One worker for the module. A 60 s fallback poll means only claims and NOTIFY wake it."""
    HANDLERS.update(TEST_HANDLERS)
    settings = Settings().model_copy(  # type: ignore[call-arg]
        update={"database_url": test_database_url, "gemini_api_key": None}
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(list(TEST_HANDLERS), 2, stop, settings=settings, poll_interval=60.0)
    )
    yield
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    for kind in TEST_HANDLERS:
        HANDLERS.pop(kind)


async def _enqueue(maker: async_sessionmaker[AsyncSession], kind: str) -> uuid.UUID:
    async with maker() as s:
        job_id = await enqueue(s, kind, {})
        await s.commit()
    assert job_id is not None
    return job_id


async def _wait_for(
    maker: async_sessionmaker[AsyncSession], job_id: uuid.UUID, done: Any, within: float = 8.0
) -> Job:
    deadline = asyncio.get_running_loop().time() + within
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
    job = await _wait_for(committed, first, lambda j: j.status == "done", within=15.0)
    assert job.result is not None
    assert job.result["ok"] is True and "duration_ms" in job.result
    assert job.finished_at is not None and job.locked_at is None

    # The worker is idle on LISTEN with a 60 s fallback poll; only NOTIFY wakes it this fast.
    second = await _enqueue(committed, "t_echo")
    await _wait_for(committed, second, lambda j: j.status == "done")


async def test_worker_handles_defer(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(committed, "t_defer")
    job = await _wait_for(committed, job_id, lambda j: j.run_after == DEFER_UNTIL)
    assert (job.status, job.attempts, job.error) == ("queued", 0, None)


async def test_worker_records_handler_error(
    worker: None, committed: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _enqueue(committed, "t_boom")
    job = await _wait_for(committed, job_id, lambda j: j.error is not None)
    assert job.error == "ValueError: nope"
    assert (job.status, job.attempts) == ("queued", 1)  # retried later with backoff
