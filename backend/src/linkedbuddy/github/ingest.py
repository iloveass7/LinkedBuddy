"""GitHub ingestion: sync the owner's repos, and poll one repo into commits and events.

Each poll reads the repo row in one short transaction, calls GitHub with no transaction open,
then writes commits, events and rating jobs in one transaction (DATABASE.md 2, ARCHITECTURE.md 4).
"""

import asyncio
import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, cast

import httpx
from sqlalchemy import Boolean, and_, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings
from linkedbuddy.db.models import Commit, Connection, Event, Repo
from linkedbuddy.enums import (
    ConnectionProvider,
    EventStatus,
    EventType,
    RepoMode,
    RepoVisibility,
)
from linkedbuddy.github.classify import Classified, classify, is_ignored
from linkedbuddy.github.client import (
    GhCommit,
    GhRelease,
    GitHubClient,
    GitHubRateLimited,
)
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer, enqueue
from linkedbuddy.security.crypto import decrypt
from linkedbuddy.triage.score import EventSignals, heuristic_score, passes_mode

MAX_COMMITS_PER_POLL = 100  # ARCHITECTURE.md 6: older commits only mark the event truncated
MAX_FILES_PER_COMMIT = 300  # GitHub's own cap
DETAIL_CONCURRENCY = 8
NEW_REPO_WINDOW = timedelta(days=30)

# A `[post]` tag anywhere in a message, or a `Post: yes` trailer line.
_LABEL_RE = re.compile(r"\[post\]|^Post:[ \t]*yes[ \t]*$", re.IGNORECASE | re.MULTILINE)


class GitHubNotConfigured(Exception):
    """No GitHub token: neither a `connections` row nor a non-blank GITHUB_TOKEN."""


async def get_github_client(
    db: AsyncSession, http: httpx.AsyncClient, settings: Settings
) -> GitHubClient:
    """Client for the stored token (decrypted here, at the point of use), else GITHUB_TOKEN."""
    enc = await db.scalar(
        select(Connection.access_token_enc).where(Connection.provider == ConnectionProvider.github)
    )
    if enc is not None:
        token = decrypt(enc, key=settings.app_encryption_key.get_secret_value())
    else:
        token = settings.github_token.get_secret_value().strip() if settings.github_token else ""
    if not token:
        raise GitHubNotConfigured("github not connected")
    return GitHubClient(http, token)


def _decide(mode: str, signals: EventSignals) -> tuple[float, EventStatus]:
    score = heuristic_score(signals)
    passed = passes_mode(RepoMode(mode), signals, score)
    return score, EventStatus.suggested if passed else EventStatus.new


def _event_row(
    repo_id: uuid.UUID,
    mode: str,
    signals: EventSignals,
    dedupe_key: str,
    stats: dict[str, Any],
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    score, status = _decide(mode, signals)
    return {
        "repo_id": repo_id,
        "type": signals.type,
        "dedupe_key": dedupe_key,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "stats": stats,
        "heuristic_score": score,
        "status": status,
    }


async def _insert_events(
    db: AsyncSession, rows: list[dict[str, Any]]
) -> dict[str, tuple[uuid.UUID, str]]:
    """Insert events, skipping dedupe keys that exist. Returns {dedupe_key: (id, status)} of new."""
    if not rows:
        return {}
    result = await db.execute(
        pg_insert(Event)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Event.id, Event.dedupe_key, Event.status)
    )
    return {key: (event_id, status) for event_id, key, status in result.all()}


async def queue_ratings(
    db: AsyncSession, events: Iterable[tuple[uuid.UUID, str]], *, never_send_to_llm: bool
) -> None:
    """Enqueue `rate_event` for newly created suggested events of a repo that allows the LLM."""
    if never_send_to_llm:
        return
    for event_id, status in events:
        if status == EventStatus.suggested:
            payload = {"event_id": str(event_id)}
            await enqueue(db, "rate_event", payload, dedupe_key=f"rate:{event_id}")


# --- sync ---------------------------------------------------------------------------------------


async def sync_repos(db: AsyncSession, gh: GitHubClient) -> int:
    """Upsert the owner's repos by `github_repo_id`. Calls GitHub before touching the DB.

    Defaults (mode, LLM opt-out) apply to new rows only; the owner's later choices are kept,
    except that a repo turning private opts out of the LLM again (SECURITY.md 6.7). A new row for
    a repo created in the last 30 days gets a `new_repo` event. Does not commit.
    """
    remote = {r.id: r for r in await gh.list_repos()}  # pages can repeat a repo
    if not remote:
        return 0
    recent = datetime.now(UTC) - NEW_REPO_WINDOW
    rows = []
    for r in remote.values():
        is_new = r.created_at is not None and r.created_at >= recent
        rows.append(
            {
                "github_repo_id": r.id,
                "full_name": r.full_name,
                "visibility": RepoVisibility.private if r.private else RepoVisibility.public,
                "mode": RepoMode.always_suggest if is_new else RepoMode.releases_and_large,
                "default_branch": r.default_branch or "main",
                "never_send_to_llm": r.private,
                "pushed_at": r.pushed_at,
            }
        )
    insert = pg_insert(Repo).values(rows)
    upsert = insert.on_conflict_do_update(
        index_elements=[Repo.github_repo_id],
        set_={
            "full_name": insert.excluded.full_name,
            "visibility": insert.excluded.visibility,
            "default_branch": insert.excluded.default_branch,
            "pushed_at": insert.excluded.pushed_at,
            "never_send_to_llm": or_(
                Repo.never_send_to_llm,
                and_(
                    insert.excluded.visibility == RepoVisibility.private,
                    Repo.visibility == RepoVisibility.public,
                ),
            ),
        },
    ).returning(
        Repo.id,
        Repo.github_repo_id,
        Repo.mode,
        Repo.never_send_to_llm,
        literal_column("xmax = 0", Boolean).label("inserted"),  # true for a fresh insert
    )
    upserted = (await db.execute(upsert)).all()

    new_repo = EventSignals(EventType.new_repo, 0, 0, False, False, False, [])
    event_rows: list[dict[str, Any]] = []
    opt_out: dict[str, bool] = {}
    for repo_id, github_id, mode, never_send, inserted in upserted:
        created = remote[github_id].created_at
        if inserted and created is not None and created >= recent:
            key = f"new_repo:{github_id}"
            event_rows.append(_event_row(repo_id, mode, new_repo, key, {}))
            opt_out[key] = never_send
    for key, event in (await _insert_events(db, event_rows)).items():
        await queue_ratings(db, [event], never_send_to_llm=opt_out[key])
    return len(upserted)


# --- poll ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _RepoSnapshot:
    id: uuid.UUID
    full_name: str
    default_branch: str
    mode: str
    never_send_to_llm: bool
    etag: str | None
    last_seen_sha: str | None
    created_at: datetime
    polled_at: datetime  # this poll's start (DB time)
    record_head_only: bool  # first poll of a repo that was not new when synced
    known_releases: frozenset[str]  # tags that already have an event


@dataclass(frozen=True, slots=True)
class _Fetched:
    not_modified: bool
    etag: str | None
    head: str | None  # new branch tip, None when unchanged
    base_sha: str | None
    commits: list[tuple[GhCommit, Classified]]
    dates: dict[str, datetime | None]  # list-endpoint dates, a fallback for missing ones
    truncated: bool
    releases: list[GhRelease]  # new ones only

    def changes(self, snap: _RepoSnapshot) -> bool:
        return bool(self.commits or self.releases or self.head or self.etag != snap.etag)


async def poll_repo(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Job `poll_repo`: new commits → one push_group (or labeled) event, plus new releases."""
    repo_id = uuid.UUID(str(payload["repo_id"]))
    async with ctx.sessionmaker() as db:
        snap = await _start(db, repo_id)
        if snap is None:
            return {"skipped": "repo not found"}
        gh = await get_github_client(db, ctx.http, ctx.settings)
    try:
        fetched = await _fetch(gh, snap)
    except GitHubRateLimited as exc:
        raise Defer(exc.reset_at) from exc
    created = 0
    if fetched.changes(snap):  # a 304 with no new release writes nothing more
        async with ctx.sessionmaker() as db:
            created = await _write(db, snap, fetched)
            await db.commit()
    return {
        "not_modified": fetched.not_modified,
        "commits": len(fetched.commits),
        "events_created": created,
    }


async def _start(db: AsyncSession, repo_id: uuid.UUID) -> _RepoSnapshot | None:
    """Read the repo and stamp last_polled_at before any GitHub call, so a repo that keeps
    failing is not re-enqueued on every scheduler tick (scheduling only). Commits."""
    repo = await db.get(Repo, repo_id)
    if repo is None:
        return None
    record_head_only = False
    if repo.last_seen_sha is None:
        new_repo_event = await db.scalar(
            select(Event.id).where(Event.dedupe_key == f"new_repo:{repo.github_repo_id}")
        )
        record_head_only = new_repo_event is None
    release_prefix = f"release:{repo.id}:"
    release_keys = await db.scalars(
        select(Event.dedupe_key).where(Event.repo_id == repo.id, Event.type == EventType.release)
    )
    known = frozenset(k.removeprefix(release_prefix) for k in release_keys)
    stamped = await db.execute(
        update(Repo)
        .where(Repo.id == repo_id)
        .values(last_polled_at=func.now())
        .returning(Repo.last_polled_at)
    )
    polled_at = cast(datetime, stamped.scalar_one())  # just set to now()
    await db.commit()
    return _RepoSnapshot(
        id=repo.id,
        full_name=repo.full_name,
        default_branch=repo.default_branch,
        mode=repo.mode,
        never_send_to_llm=repo.never_send_to_llm,
        etag=repo.etag,
        last_seen_sha=repo.last_seen_sha,
        created_at=repo.created_at,
        polled_at=polled_at,
        record_head_only=record_head_only,
        known_releases=known,
    )


def _is_new_release(rel: GhRelease, snap: _RepoSnapshot) -> bool:
    # Only releases published since the repo was added: a first sync must not flood.
    return (
        not rel.draft
        and rel.published_at is not None
        and rel.published_at > snap.created_at
        and rel.tag_name not in snap.known_releases
    )


async def _fetch(gh: GitHubClient, snap: _RepoSnapshot) -> _Fetched:
    """All GitHub calls of a poll.

    The commit listing has no `since` (it filters on committer date, so a commit pushed long
    after it was made would be skipped forever) and a stable URL, so its ETag gives 304s.
    Releases are checked on every poll, 304 included.
    """
    page = await gh.list_commits(snap.full_name, snap.default_branch, None, snap.etag)
    releases = [r for r in await gh.list_releases(snap.full_name) if _is_new_release(r, snap)]
    tip = page.items[0].sha if page.items else None
    if tip is None or tip == snap.last_seen_sha:
        return _Fetched(page.not_modified, page.etag, None, None, [], {}, False, releases)
    if snap.record_head_only:
        # The first look at an old repo: its history is not one event.
        return _Fetched(False, page.etag, tip, None, [], {}, False, releases)

    shas, base_sha, truncated = await _new_shas(gh, snap, [r.sha for r in page.items])
    commits = []
    for detail in await _get_commits(gh, snap.full_name, shas):
        if len(detail.files) > MAX_FILES_PER_COMMIT:
            detail = detail.model_copy(update={"files": detail.files[:MAX_FILES_PER_COMMIT]})
            truncated = True
        commits.append((detail, classify(detail)))
    dates = {r.sha: r.committed_at for r in page.items}
    return _Fetched(False, page.etag, tip, base_sha, commits, dates, truncated, releases)


async def _new_shas(
    gh: GitHubClient, snap: _RepoSnapshot, page: list[str]
) -> tuple[list[str], str | None, bool]:
    """(new commit shas newest first, at most 100; base sha; truncated) for a moved branch tip.

    `page` is the newest first page of the branch listing, which starts at the new tip.
    """
    last = snap.last_seen_sha
    if last is None:  # first poll of a new repo: its recent history is one event
        return page[:MAX_COMMITS_PER_POLL], None, len(page) >= MAX_COMMITS_PER_POLL
    if last in page:
        return page[: page.index(last)], last, False
    between = await gh.compare(snap.full_name, last, page[0])
    if between is None:  # 404: the old tip is gone (force-push), so the event has no base
        return page[:MAX_COMMITS_PER_POLL], None, len(page) >= MAX_COMMITS_PER_POLL
    if len(between) < MAX_COMMITS_PER_POLL:
        # The complete range (oldest first); the page missed the old tip in a non-linear history.
        return between[::-1], last, False
    # 100+ new commits. The client reads compare's first page, which holds the OLDEST 100; the
    # listing page holds the newest, which are the ones to keep (ARCHITECTURE.md 6).
    return page[:MAX_COMMITS_PER_POLL], last, True


async def _get_commits(gh: GitHubClient, full_name: str, shas: list[str]) -> list[GhCommit]:
    """Commit details, at most DETAIL_CONCURRENCY in flight. The first error cancels the rest."""
    gate = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def one(sha: str) -> GhCommit:
        async with gate:
            return await gh.get_commit(full_name, sha)

    tasks = [asyncio.ensure_future(one(sha)) for sha in shas]
    try:
        return list(await asyncio.gather(*tasks))
    finally:
        for task in tasks:
            task.cancel()


def _push_signals(
    commits: Sequence[tuple[GhCommit, Classified]], truncated: bool
) -> tuple[EventSignals, dict[str, Any]]:
    names: set[str] = set()
    additions = deletions = new_files = 0
    readme_changed = False
    for c, _ in commits:
        for f in c.files:
            names.add(f.filename)
            additions += f.additions
            deletions += f.deletions
            new_files += f.status == "added" and not is_ignored(f.filename)
            readme_changed |= PurePosixPath(f.filename).name.lower().startswith("readme")
    kinds: list[str] = [k.kind for _, k in commits]
    labeled = any(_LABEL_RE.search(c.message) for c, _ in commits)
    meaningful = sum(k.meaningful for _, k in commits)
    signals = EventSignals(
        type=EventType.labeled if labeled else EventType.push_group,
        meaningful=meaningful,
        new_files=new_files,
        has_feat="feature" in kinds,
        readme_changed=readme_changed,
        labeled=labeled,
        kinds=kinds,
    )
    stats = {
        "commits": len(commits),
        "files": len(names),
        "additions": additions,
        "deletions": deletions,
        "meaningful": meaningful,
        "kinds": dict(Counter(kinds)),
        "truncated": truncated,
    }
    return signals, stats


async def _write(db: AsyncSession, snap: _RepoSnapshot, fetched: _Fetched) -> int:
    """Upsert events and commits, queue ratings, advance the repo cursor. Does not commit."""
    rows = []
    push_key = f"push:{snap.id}:{fetched.head}"
    if fetched.commits:
        signals, stats = _push_signals(fetched.commits, fetched.truncated)
        rows.append(
            _event_row(snap.id, snap.mode, signals, push_key, stats, fetched.base_sha, fetched.head)
        )
    release_signals = EventSignals(EventType.release, 0, 0, False, False, False, [])
    for rel in fetched.releases:
        stats = {"tag": rel.tag_name, "name": rel.name, "prerelease": rel.prerelease}
        key = f"release:{snap.id}:{rel.tag_name}"
        rows.append(_event_row(snap.id, snap.mode, release_signals, key, stats))
    created = await _insert_events(db, rows)

    if fetched.commits:
        push_id = created[push_key][0] if push_key in created else None
        if push_id is None:
            push_id = await db.scalar(select(Event.id).where(Event.dedupe_key == push_key))
        await db.execute(
            pg_insert(Commit)
            .values(
                [
                    {
                        "repo_id": snap.id,
                        "sha": c.sha,
                        "event_id": push_id,
                        "classification": k.kind,
                        "meaningful_lines": k.meaningful,
                        "author_login": c.author_login,
                        "message": c.message,
                        "files": [f.model_dump() for f in c.files],
                        "committed_at": c.committed_at
                        or fetched.dates.get(c.sha)
                        or snap.polled_at,
                    }
                    for c, k in fetched.commits
                ]
            )
            .on_conflict_do_nothing(index_elements=["repo_id", "sha"])
        )
    await queue_ratings(db, created.values(), never_send_to_llm=snap.never_send_to_llm)

    cursor: dict[str, Any] = {"etag": fetched.etag, "last_polled_at": snap.polled_at}
    if fetched.head is not None:
        cursor["last_seen_sha"] = fetched.head
    await db.execute(update(Repo).where(Repo.id == snap.id).values(**cursor))
    return len(created)


HANDLERS["poll_repo"] = poll_repo
