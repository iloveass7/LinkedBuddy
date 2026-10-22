"""Worker loop (ARCHITECTURE.md 7): LISTEN for new jobs, claim, run handlers, record outcomes.

Logs carry job ids, kinds, durations and exception types only, never payloads or results
(SECURITY.md 7).
"""

import asyncio
import logging
import time
from dataclasses import replace
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings, get_settings
from linkedbuddy.db.engine import engine_for, make_sessionmaker
from linkedbuddy.db.models import Job
from linkedbuddy.http import make_http_client
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer, claim, complete, defer, deferral_count, fail
from linkedbuddy.llm.gemini import make_llm

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0
DRAIN_TIMEOUT_S = 30.0
ERROR_MAX_CHARS = 500


async def run_worker(
    kinds: list[str],
    concurrency: int,
    stop: asyncio.Event,
    *,
    settings: Settings | None = None,
    poll_interval: float = POLL_INTERVAL_S,
) -> None:
    """Run jobs of `kinds` until `stop` is set, then drain in-flight jobs (30 s at most)."""
    settings = settings or get_settings()
    engine = engine_for(settings)
    http = make_http_client()
    ctx = JobContext(make_sessionmaker(engine), http, make_llm(settings), settings)
    wake = asyncio.Event()
    listener = await _listen(settings.database_url, kinds, wake, stop)
    try:
        await _serve(ctx, kinds, concurrency, stop, wake, poll_interval)
    finally:
        if listener is not None:
            await listener.close()
        await http.aclose()
        await engine.dispose()


async def _listen(
    url: str, kinds: list[str], wake: asyncio.Event, stop: asyncio.Event
) -> asyncpg.Connection | None:
    """Dedicated LISTEN connection (outside the pool). Without it the worker just polls."""

    def on_notify(*_: Any) -> None:
        wake.set()

    def on_lost(*_: Any) -> None:
        if not stop.is_set():  # not our own close on shutdown
            log.warning("LISTEN connection lost; falling back to polling")

    dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        conn: asyncpg.Connection = await asyncpg.connect(dsn)
        for kind in kinds:
            await conn.add_listener(f"jobs_{kind}", on_notify)
    except (OSError, asyncpg.PostgresError) as exc:
        log.warning("LISTEN unavailable (%s); polling only", type(exc).__name__)
        return None
    conn.add_termination_listener(on_lost)
    return conn


async def _serve(
    ctx: JobContext,
    kinds: list[str],
    concurrency: int,
    stop: asyncio.Event,
    wake: asyncio.Event,
    poll_interval: float,
) -> None:
    slots = asyncio.Semaphore(concurrency)
    running: set[asyncio.Task[None]] = set()
    while not stop.is_set():
        await _first(stop, asyncio.ensure_future(slots.acquire()), limit_s=None)
        if stop.is_set():
            break  # the slot (if acquired) is never released; nothing more is claimed
        wake.clear()  # before claiming, so a NOTIFY during the claim is not lost
        job = await _claim_one(ctx.sessionmaker, kinds)
        if job is None:
            slots.release()
            await _first(stop, asyncio.ensure_future(wake.wait()), limit_s=poll_interval)
            continue
        task = asyncio.create_task(_run(ctx, job, slots))
        running.add(task)
        task.add_done_callback(running.discard)

    if running:
        log.info("draining %d job(s)", len(running))
        _, pending = await asyncio.wait(running, timeout=DRAIN_TIMEOUT_S)
        for task in pending:  # left `running`; the scheduler's stale-lock rule reclaims them
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _first(stop: asyncio.Event, other: asyncio.Future[Any], limit_s: float | None) -> None:
    """Wait until `stop` is set, `other` finishes, or `limit_s` seconds pass."""
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({stopper, other}, timeout=limit_s, return_when=asyncio.FIRST_COMPLETED)
    stopper.cancel()
    if not other.done():
        other.cancel()


async def _claim_one(sm: async_sessionmaker[AsyncSession], kinds: list[str]) -> Job | None:
    try:
        async with sm() as db:
            job = await claim(db, kinds)
            await db.commit()
            return job
    except Exception as exc:  # DB hiccup: retry on the next poll, never crash the loop
        log.warning("claim failed: %s", type(exc).__name__)
        return None


async def _run(ctx: JobContext, job: Job, slots: asyncio.Semaphore) -> None:
    started = time.monotonic()
    try:
        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise LookupError(f"no handler for job kind {job.kind!r}")
            result = await handler(replace(ctx, deferrals=deferral_count(job)), job.payload)
            if not isinstance(result, dict):
                raise TypeError(f"handler result must be a dict, got {type(result).__name__}")
            duration_ms = int((time.monotonic() - started) * 1000)
            # Inside the handled path: an unserializable result fails the attempt cleanly.
            async with ctx.sessionmaker() as db:
                saved = await complete(
                    db, job.id, {**result, "duration_ms": duration_ms}, attempts=job.attempts
                )
                await db.commit()
            outcome = f"done duration_ms={duration_ms}"
        except Defer as d:
            async with ctx.sessionmaker() as db:
                saved = await defer(db, job.id, d.until, attempts=job.attempts)
                await db.commit()
            outcome = "deferred"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:ERROR_MAX_CHARS]}"
            async with ctx.sessionmaker() as db:
                saved = await fail(db, job.id, error, attempts=job.attempts)
                await db.commit()
            outcome = f"failed error_type={type(exc).__name__}"
        if saved:
            log.info("job %s id=%s kind=%s attempt=%d", outcome, job.id, job.kind, job.attempts)
        else:
            log.warning(
                "stale outcome ignored id=%s kind=%s attempt=%d", job.id, job.kind, job.attempts
            )
    except Exception as exc:  # could not record the outcome; the stale-lock rule retries it
        log.error("job outcome not saved id=%s kind=%s: %s", job.id, job.kind, type(exc).__name__)
    finally:
        slots.release()
