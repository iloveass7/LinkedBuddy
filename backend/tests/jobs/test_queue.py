from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.db.models import AuthSession, Draft, Event, Job, Repo
from linkedbuddy.jobs.queue import claim, complete, defer, enqueue, fail
from linkedbuddy.scheduler import tick

KIND = "test_kind"


async def test_enqueue_dedupe(db: AsyncSession) -> None:
    first = await enqueue(db, KIND, {"n": 1}, dedupe_key="poll:1")
    assert first is not None
    assert await enqueue(db, KIND, {"n": 2}, dedupe_key="poll:1") is None
    await db.execute(update(Job).where(Job.id == first).values(status="done"))
    assert await enqueue(db, KIND, {"n": 3}, dedupe_key="poll:1") is not None  # only live jobs


async def test_claim_skip_locked(committed: async_sessionmaker[AsyncSession]) -> None:
    async with committed() as s:
        await enqueue(s, KIND, {})
        await enqueue(s, KIND, {})
        await s.commit()
    async with committed() as a, committed() as b, committed() as c:
        ja = await claim(a, [KIND])  # a's transaction stays open and holds its row lock
        jb = await claim(b, [KIND])
        assert ja is not None and jb is not None
        assert ja.id != jb.id
        assert ja.status == "running" and ja.attempts == 1 and ja.locked_at is not None
        assert await claim(c, [KIND]) is None


async def test_claim_respects_run_after(committed: async_sessionmaker[AsyncSession]) -> None:
    async with committed() as s:
        await enqueue(s, KIND, {}, run_after=datetime.now(UTC) + timedelta(hours=1))
        await s.commit()
        assert await claim(s, [KIND]) is None
        assert await claim(s, ["other_kind"]) is None


async def test_fail_retries_then_fails(committed: async_sessionmaker[AsyncSession]) -> None:
    async with committed() as s:
        job_id = await enqueue(s, KIND, {})
        await s.commit()
        for attempt in (1, 2, 3):
            job = await claim(s, [KIND])
            assert job is not None and job.attempts == attempt
            assert await fail(s, job.id, "RuntimeError: boom", attempts=job.attempts)
            await s.commit()
            job = await s.get(Job, job_id, populate_existing=True)
            assert job is not None
            if attempt == 1:
                assert job.status == "queued"
                backoff = job.run_after - datetime.now(UTC)  # 30 s * 2**1
                assert timedelta(seconds=50) < backoff <= timedelta(seconds=60)
            if attempt < 3:
                retry_now = update(Job).where(Job.id == job_id).values(run_after=job.created_at)
                await s.execute(retry_now)
                await s.commit()
        assert job.status == "failed"
        assert job.error == "RuntimeError: boom"
        assert job.finished_at is not None
        assert await claim(s, [KIND]) is None


async def test_defer_does_not_consume_attempt(committed: async_sessionmaker[AsyncSession]) -> None:
    until = datetime.now(UTC) + timedelta(minutes=5)
    async with committed() as s:
        job_id = await enqueue(s, KIND, {})
        await s.commit()
        job = await claim(s, [KIND])
        assert job is not None and job.attempts == 1
        assert await defer(s, job.id, until, attempts=job.attempts)
        await s.commit()
        job = await s.get(Job, job_id, populate_existing=True)
        assert job is not None
        assert (job.status, job.attempts, job.run_after, job.locked_at) == (
            "queued",
            0,
            until,
            None,
        )


async def test_outcome_ignored_after_reclaim(db: AsyncSession) -> None:
    job_id = await enqueue(db, "t_reclaim", {})
    old = await claim(db, ["t_reclaim"])
    assert old is not None and old.attempts == 1
    # The scheduler reclaims the stale run and another worker claims it again.
    await db.execute(update(Job).where(Job.id == job_id).values(status="queued", locked_at=None))
    new = await claim(db, ["t_reclaim"])
    assert new is not None and new.attempts == 2

    assert await complete(db, job_id, {"ok": True}, attempts=1) is False
    assert await fail(db, job_id, "late", attempts=1) is False
    assert await defer(db, job_id, datetime.now(UTC), attempts=1) is False
    job = await db.get(Job, job_id, populate_existing=True)
    assert job is not None
    assert (job.status, job.attempts, job.result, job.error) == ("running", 2, None, None)
    assert await complete(db, job_id, {"ok": True}, attempts=2) is True


async def _repo(db: AsyncSession, n: int, mode: str, last_polled_at: datetime | None) -> Repo:
    repo = Repo(
        github_repo_id=n,
        full_name=f"me/r{n}",
        visibility="public",
        mode=mode,
        default_branch="main",
        last_polled_at=last_polled_at,
    )
    db.add(repo)
    return repo


async def test_stale_lock_reclaimed(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    stale = Job(kind=KIND, status="running", attempts=1, locked_at=now - timedelta(minutes=11))
    spent = Job(kind=KIND, status="running", attempts=3, locked_at=now - timedelta(minutes=11))
    fresh = Job(kind=KIND, status="running", attempts=1, locked_at=now - timedelta(minutes=2))
    db.add_all([stale, spent, fresh])
    await db.flush()
    report = await tick(db, now, settings=settings)
    assert (report.stale_requeued, report.stale_failed) == (1, 1)
    for job in (stale, spent, fresh):
        await db.refresh(job)
    assert (stale.status, stale.locked_at) == ("queued", None)
    assert spent.status == "failed" and spent.error
    assert fresh.status == "running"


async def test_video_stale_window(db: AsyncSession, settings: Settings) -> None:
    assert settings.video_timeout_s == 600  # window = 600 + 300 s = 15 min
    now = datetime.now(UTC)
    rendering = Job(
        kind="render_video", status="running", attempts=1, locked_at=now - timedelta(minutes=11)
    )
    stuck = Job(
        kind="render_video", status="running", attempts=1, locked_at=now - timedelta(minutes=16)
    )
    db.add_all([rendering, stuck])
    await db.flush()
    await tick(db, now, settings=settings)
    await db.refresh(rendering)
    await db.refresh(stuck)
    assert rendering.status == "running"
    assert stuck.status == "queued"


async def test_scheduler_skips_muted_and_recent(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    due_never = await _repo(db, 1, "always_suggest", None)
    due_old = await _repo(db, 2, "manual_only", now - timedelta(hours=4))
    await _repo(db, 3, "muted", None)
    await _repo(db, 4, "releases_and_large", now - timedelta(hours=1))
    await db.flush()

    report = await tick(db, now, settings=settings)
    assert report.polls_enqueued == 2
    jobs = (await db.scalars(select(Job).where(Job.kind == "poll_repo"))).all()
    assert sorted(j.dedupe_key or "" for j in jobs) == sorted(
        [f"poll:{due_never.id}", f"poll:{due_old.id}"]
    )
    assert {j.payload["repo_id"] for j in jobs} == {str(due_never.id), str(due_old.id)}
    again = await tick(db, now, settings=settings)
    assert again.polls_enqueued == 0  # deduped while queued


async def test_scheduler_purges_retention(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    db.add_all(
        [
            AuthSession(token_hash=b"expired-test-session", expires_at=now - timedelta(hours=1)),
            Job(kind=KIND, status="done", finished_at=now - timedelta(days=8)),
            Job(kind=KIND, status="done", finished_at=now - timedelta(days=1)),
            Job(kind=KIND, status="failed", finished_at=now - timedelta(days=31)),
            Job(kind=KIND, status="failed", finished_at=now - timedelta(days=8)),
        ]
    )
    await db.flush()
    report = await tick(db, now, settings=settings)
    assert report.sessions_deleted >= 1
    assert report.done_jobs_deleted >= 1
    assert report.failed_jobs_deleted >= 1
    left = (await db.scalars(select(Job.status).where(Job.kind == KIND))).all()
    assert sorted(left) == ["done", "failed"]


async def _event_with_draft(db: AsyncSession, repo: Repo, key: str, drafted_at: datetime) -> Event:
    event = Event(repo_id=repo.id, type="manual", dedupe_key=key, context_json={"readme": "x"})
    db.add(event)
    await db.flush()
    db.add(
        Draft(
            event_id=event.id,
            tone="casual",
            body="b",
            model="m",
            prompt_version="v1",
            created_at=drafted_at,
        )
    )
    return event


async def test_scheduler_clears_old_context(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    repo = await _repo(db, 5, "muted", None)
    await db.flush()
    old = await _event_with_draft(db, repo, "ctx:old", now - timedelta(days=8))
    recent = await _event_with_draft(db, repo, "ctx:recent", now - timedelta(days=8))
    db.add(Draft(event_id=recent.id, tone="casual", body="b", model="m", prompt_version="v1"))
    await db.flush()

    report = await tick(db, now, settings=settings)
    assert report.contexts_cleared >= 1
    await db.refresh(old)
    await db.refresh(recent)
    assert old.context_json is None
    assert recent.context_json == {"readme": "x"}  # its newest draft is fresh


async def test_scheduler_resets_stuck_generating(db: AsyncSession, settings: Settings) -> None:
    now = datetime.now(UTC)
    repo = await _repo(db, 6, "muted", None)
    await db.flush()
    events = {
        name: Event(repo_id=repo.id, type="manual", dedupe_key=f"gen:{name}", status=status)
        for name, status in [
            ("stuck", "generating"),
            ("queued", "generating"),
            ("running", "generating"),
            ("other_job_done", "generating"),
            ("drafted", "drafted"),
        ]
    }
    db.add_all(events.values())
    await db.flush()
    db.add_all(
        [
            Job(
                kind="generate_draft",
                status="queued",
                payload={"event_id": str(events["queued"].id)},
            ),
            Job(
                kind="generate_draft",
                status="running",
                locked_at=now,
                payload={"event_id": str(events["running"].id)},
            ),
            Job(
                kind="generate_draft",
                status="done",
                payload={"event_id": str(events["other_job_done"].id)},
            ),
            Job(
                kind="render_diagram",
                status="queued",
                payload={"event_id": str(events["stuck"].id)},
            ),
        ]
    )
    await db.flush()

    report = await tick(db, now, settings=settings)

    assert report.generating_reset == 2
    for event in events.values():
        await db.refresh(event)
    assert {name: e.status for name, e in events.items()} == {
        "stuck": "suggested",
        "queued": "generating",
        "running": "generating",
        "other_job_done": "suggested",
        "drafted": "drafted",
    }
