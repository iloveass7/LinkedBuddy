"""Repo and GitHub-connection operations behind the repo routes."""

import uuid
from collections.abc import Sequence
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings
from linkedbuddy.db.models import Connection, Event, Job, Repo
from linkedbuddy.enums import (
    ConnectionProvider,
    EventStatus,
    EventType,
    JobStatus,
    RepoVisibility,
)
from linkedbuddy.github.ingest import get_github_client, queue_ratings, sync_repos
from linkedbuddy.jobs.queue import enqueue
from linkedbuddy.security.crypto import encrypt
from linkedbuddy.triage.score import EventSignals, heuristic_score


class PrivateRepoNeedsAck(Exception):
    """Turning the LLM on for a private repo needs `acknowledge_free_tier_data_use`.

    The Gemini free tier may use prompts to improve Google's products (SECURITY.md 6.7).
    """


async def set_github_token(db: AsyncSession, token: str, settings: Settings) -> None:
    """Store (or replace) the GitHub token, encrypted. Commits."""
    enc = encrypt(token, key=settings.app_encryption_key.get_secret_value())
    stmt = pg_insert(Connection).values(provider=ConnectionProvider.github, access_token_enc=enc)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[Connection.provider], set_={"access_token_enc": enc}
        )
    )
    await db.commit()


async def sync(db: AsyncSession, http: httpx.AsyncClient, settings: Settings) -> int:
    """Raises GitHubNotConfigured or GitHubRateLimited. Commits."""
    gh = await get_github_client(db, http, settings)
    await db.commit()  # close the token read before calling GitHub (DATABASE.md 2)
    synced = await sync_repos(db, gh)
    await db.commit()
    return synced


async def list_repos(db: AsyncSession) -> Sequence[Any]:
    """Rows of the columns `RepoOut` needs, by name."""
    result = await db.execute(
        select(
            Repo.id,
            Repo.full_name,
            Repo.visibility,
            Repo.mode,
            Repo.never_send_to_llm,
            Repo.demo_url,
            Repo.last_polled_at,
        ).order_by(Repo.full_name, Repo.id)
    )
    return result.all()


async def update_repo(
    db: AsyncSession, repo_id: uuid.UUID, changes: dict[str, Any], *, acknowledged: bool
) -> Repo | None:
    """Apply `changes` (mode, demo_url, never_send_to_llm). None if the repo is missing. Commits."""
    repo = await db.get(Repo, repo_id)
    if repo is None:
        return None
    if (
        changes.get("never_send_to_llm") is False
        and repo.visibility == RepoVisibility.private
        and not acknowledged
    ):
        raise PrivateRepoNeedsAck
    for field, value in changes.items():
        setattr(repo, field, value)
    await db.commit()
    return repo


async def request_poll(db: AsyncSession, repo_id: uuid.UUID) -> uuid.UUID | None:
    """Queue `poll_repo`, or return the poll already queued or running. None if no repo. Commits."""
    if await db.scalar(select(Repo.id).where(Repo.id == repo_id)) is None:
        return None
    key = f"poll:{repo_id}"
    job_id = await enqueue(db, "poll_repo", {"repo_id": str(repo_id)}, dedupe_key=key)
    if job_id is None:
        job_id = await db.scalar(
            select(Job.id)
            .where(
                Job.dedupe_key == key,
                Job.status.in_([JobStatus.queued, JobStatus.running]),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    await db.commit()
    return job_id


async def create_manual_event(
    db: AsyncSession, repo_id: uuid.UUID, base_sha: str | None, head_sha: str | None
) -> Event | None:
    """Owner-requested event, always suggested. None if the repo is missing. Commits."""
    repo = await db.get(Repo, repo_id)
    if repo is None:
        return None
    signals = EventSignals(EventType.manual, 0, 0, False, False, False, [])
    inserted = await db.execute(
        pg_insert(Event)
        .values(
            repo_id=repo_id,
            type=EventType.manual,
            base_sha=base_sha,
            head_sha=head_sha,
            dedupe_key=f"manual:{repo_id}:{uuid.uuid4()}",
            heuristic_score=heuristic_score(signals),
            status=EventStatus.suggested,
        )
        .returning(Event)
    )
    event: Event = inserted.scalar_one()
    await queue_ratings(db, [(event.id, event.status)], never_send_to_llm=repo.never_send_to_llm)
    await db.commit()
    return event
