"""Context builder: the exact repository text a draft prompt may see (SECURITY.md 6).

Repository text is untrusted. Every evidence entry is redacted, tagged with an id the model must
cite, and rendered only inside ``<repo_context>`` delimiters that repo text cannot close. The
privacy mode is enforced here, not in the prompt. The package is trimmed to a hard character
budget and cached on the event (ARCHITECTURE.md 6, Gemini).

Commit messages and file lists come from the DB (ingestion stored them). GitHub is asked only for
what the DB lacks: the README at head, releases, the tree at head and, in ``full`` mode, patches.
A manual event links no commits: its base..head range is resolved with ``compare`` and any commit
of the range that no poll stored is fetched (never written to ``commits``). Never log evidence text.
"""

import asyncio
import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import Commit, Draft, Event, Post, Repo
from linkedbuddy.enums import EventType, PrivacyMode, RepoVisibility
from linkedbuddy.github.client import GhCommit, GhRelease, GitHubClient
from linkedbuddy.security.redact import RedactionBlocked, redact

CONTEXT_VERSION = "3"  # bump whenever the builder's output changes: it invalidates every cache
CONTEXT_CHAR_BUDGET = 24_000
RATING_SUMMARY_LIMIT = 4_000

OPEN, CLOSE = "<repo_context>", "</repo_context>"
TREE_HEADER = "file_tree:"

README_HEAD_LINES = 150
MAX_README_LINE_CHARS = 500
MAX_TREE_PATHS = 300
MAX_COMMIT_MESSAGE_CHARS = 1_000
MAX_FILES_LISTED = 20
MAX_PATCH_COMMITS = 10
MAX_PATCH_FILES = 20
PATCH_EXCERPT_LINES = 40
MAX_RELEASES = 5
MAX_RELEASE_CHARS = 800
MAX_PREV_POST_CHARS = 3_000
MAX_STATS_CHARS = 500
# A manual event's range, like a poll (ARCHITECTURE.md 6): the newest 100 commits, details 8 at a
# time, at most 300 files each.
MAX_RANGE_COMMITS = 100
RANGE_DETAIL_CONCURRENCY = 8
MAX_FILES_PER_COMMIT = 300

# Rating summary sections (chars of rendered entries).
SUMMARY_GOALS_CHARS = 600
SUMMARY_README_LINES = 40
SUMMARY_README_CHARS = 1_200
SUMMARY_RELEASE_CHARS = 600
SUMMARY_ENTRY_CHARS = 300

EvidenceKind = Literal["readme", "commit", "file", "release", "prev_post", "repo"]

# Any spelling of the tag a model might honour: spaces, an escaped slash, attributes, and no
# closing ">" at all (an unterminated "</repo_context" still reads as a close). Attributes stop at
# a line end. Fullwidth and other compatibility forms are folded to ASCII by NFKC first.
_DELIMITER_RE = re.compile(r"<\s*\\?/?\s*repo_context\b[^>\n]*>?", re.IGNORECASE)
# Redaction kinds that block a private repo. Emails are still redacted but do not block.
_NON_BLOCKING_KINDS = frozenset({"email"})
_KEY_END_RE = re.compile(r"-----END [A-Z ]*PRIVATE KEY")


class Evidence(BaseModel):
    id: str
    kind: EvidenceKind
    text: str


def _strip_delimiters(text: str) -> str:
    """NFKC-normalise, then remove delimiter tags until none remain (nested ones included)."""
    text = unicodedata.normalize("NFKC", text)
    while True:
        cleaned = _DELIMITER_RE.sub("", text)
        if cleaned == text:
            return cleaned
        text = cleaned


def _entry(e: Evidence) -> str:
    # Continuation lines are indented so repo text cannot start a line that looks like an id.
    return "\n  ".join(_strip_delimiters(f"[{e.id}] {e.text}").splitlines())


def _tree_line(path: str) -> str:
    # Indented like continuation lines: a path such as "[readme:L1] ..." cannot pose as evidence.
    return "  " + " ".join(_strip_delimiters(path).splitlines())


class ContextPackage(BaseModel):
    repo_full_name: str
    description: str | None
    languages: list[str]
    topics: list[str]
    evidence: list[Evidence]
    file_tree: list[str]
    previous_post: str | None
    privacy_mode: PrivacyMode

    def ids(self) -> set[str]:
        return {e.id for e in self.evidence}

    def render(self) -> str:
        parts = [OPEN, *(_entry(e) for e in self.evidence)]
        if self.file_tree:
            parts += [TREE_HEADER, *(_tree_line(p) for p in self.file_tree)]
        parts.append(CLOSE)
        return "\n".join(parts)


def context_hash(head_sha: str | None, privacy_mode: PrivacyMode) -> str:
    return hashlib.sha256(f"{head_sha}|{privacy_mode}|{CONTEXT_VERSION}".encode()).hexdigest()


class _Redactor:
    """Strips delimiters, redacts each string and remembers which kinds fired."""

    def __init__(self) -> None:
        self.fired: set[str] = set()

    def __call__(self, text: str) -> str:
        result = redact(_strip_delimiters(text))
        self.fired.update(result.kinds)
        return result.text


def _readme_evidence(readme: str, clean: _Redactor) -> list[Evidence]:
    """One entry per kept non-empty line: lines 1-150 plus Markdown table lines beyond."""
    out: list[Evidence] = []
    in_key = False
    for n, raw in enumerate(readme.splitlines(), 1):
        if in_key:  # body of a private key block whose BEGIN line was redacted: drop it
            in_key = not _KEY_END_RE.search(raw)
            continue
        if n > README_HEAD_LINES and not raw.lstrip().startswith("|"):
            continue
        if not raw.strip():
            continue
        result = redact(_strip_delimiters(raw))
        clean.fired.update(result.kinds)
        # A BEGIN line without its END: redact() blanked this line; skip the key body below.
        in_key = "private_key" in result.kinds and not _KEY_END_RE.search(raw)
        out.append(
            Evidence(id=f"readme:L{n}", kind="readme", text=result.text[:MAX_README_LINE_CHARS])
        )
    return out


def _file_names(files: Iterable[Any]) -> list[str]:
    names = []
    for f in files:
        name = f.get("filename") if isinstance(f, dict) else f
        if isinstance(name, str):
            names.append(name)
    return names


def _commit_evidence(rows: Sequence[Any], clean: _Redactor) -> list[Evidence]:
    """Oldest first, so the budget trims from the front."""
    out: list[Evidence] = []
    seen: set[str] = set()
    for row in rows:
        ev_id = f"commit:{row.sha[:7]}"
        if ev_id in seen:
            continue
        seen.add(ev_id)
        text = clean(row.message.strip())[:MAX_COMMIT_MESSAGE_CHARS]
        names = _file_names(row.files or [])
        if names:
            listed = ", ".join(names[:MAX_FILES_LISTED])
            more = len(names) - MAX_FILES_LISTED
            text += "\nfiles: " + clean(listed) + (f" (+{more} more)" if more > 0 else "")
        out.append(Evidence(id=ev_id, kind="commit", text=text))
    return out


@dataclass(frozen=True, slots=True)
class _CommitRow:
    """A fetched commit in the shape of a stored `commits` row (files without patches)."""

    sha: str
    message: str
    files: list[dict[str, Any]]


def _as_row(commit: GhCommit) -> _CommitRow:
    files = [f.model_dump() for f in commit.files[:MAX_FILES_PER_COMMIT]]
    return _CommitRow(sha=commit.sha, message=commit.message, files=files)


async def _commit_or_none(gh: GitHubClient, full_name: str, sha: str) -> GhCommit | None:
    try:
        return await gh.get_commit(full_name, sha)
    except httpx.HTTPStatusError as exc:  # commit gone (force-push): no evidence, no failure
        if exc.response.status_code in (404, 422):
            return None
        raise


async def _range_shas(
    gh: GitHubClient, full_name: str, base_sha: str | None, head_sha: str
) -> list[str]:
    """A manual event's commits, oldest first: base..head, or just head without a usable base."""
    if base_sha:
        between = await gh.compare(full_name, base_sha, head_sha)
        if between:
            return between[-MAX_RANGE_COMMITS:]
    return [head_sha]


async def _range_commits(
    db: AsyncSession,
    gh: GitHubClient,
    repo_id: uuid.UUID,
    full_name: str,
    base_sha: str | None,
    head_sha: str,
) -> tuple[list[Any], dict[str, GhCommit]]:
    """(commit rows oldest first, details fetched on the way). Stored rows are used when present.

    Holds no transaction across the GitHub calls.
    """
    shas = await _range_shas(gh, full_name, base_sha, head_sha)
    stored = {
        r.sha: r
        for r in (
            await db.execute(
                select(Commit.sha, Commit.message, Commit.files).where(
                    Commit.repo_id == repo_id, Commit.sha.in_(shas)
                )
            )
        ).all()
    }
    await db.commit()
    gate = asyncio.Semaphore(RANGE_DETAIL_CONCURRENCY)

    async def one(sha: str) -> GhCommit | None:
        async with gate:
            return await _commit_or_none(gh, full_name, sha)

    missing = [s for s in shas if s not in stored]
    tasks = [asyncio.ensure_future(one(s)) for s in missing]
    try:
        details = await asyncio.gather(*tasks)
    finally:  # the first error (e.g. a rate limit) cancels the rest
        for task in tasks:
            task.cancel()
    fetched = {s: c for s, c in zip(missing, details, strict=True) if c}
    rows = [stored.get(s) or (_as_row(fetched[s]) if s in fetched else None) for s in shas]
    return [r for r in rows if r is not None], fetched


def _release_evidence(
    releases: Sequence[GhRelease], event_tag: str | None, clean: _Redactor
) -> list[Evidence]:
    published = [r for r in releases if not r.draft]
    published.sort(key=lambda r: r.tag_name != event_tag)  # the event's own release first
    out: list[Evidence] = []
    seen: set[str] = set()
    for r in published[:MAX_RELEASES]:
        tag = clean(r.tag_name)
        if tag in seen:
            continue
        seen.add(tag)
        text = r.tag_name + (f" {r.name}" if r.name else "")
        if r.body:
            text += "\n" + r.body.strip()
        out.append(
            Evidence(id=f"release:{tag}", kind="release", text=clean(text)[:MAX_RELEASE_CHARS])
        )
    return out


def _patch_evidence(commits: Sequence[GhCommit | None], clean: _Redactor) -> list[Evidence]:
    """One excerpt per path (newest commit wins), at most MAX_PATCH_FILES."""
    out: list[Evidence] = []
    seen: set[str] = set()
    for commit in reversed(commits):
        for f in commit.files if commit else []:
            if not f.patch or len(out) >= MAX_PATCH_FILES:
                continue
            path = clean(f.filename)
            if path in seen:
                continue
            seen.add(path)
            excerpt = "\n".join(clean(f.patch).splitlines()[:PATCH_EXCERPT_LINES])
            header = f"{path} ({f.status}, +{f.additions} -{f.deletions})"
            out.append(Evidence(id=f"file:{path}", kind="file", text=f"{header}\n{excerpt}"))
    return out


def _fit(pkg: ContextPackage, budget: int) -> ContextPackage:
    """Drop evidence until ``len(pkg.render()) <= budget``.

    Order: patch excerpts, README beyond line 150, oldest commits, README from the end, the file
    tree from the end, then (last resort) releases and the previous post. Sizes are tracked
    incrementally: render() joins parts with one newline each.
    """
    size = len(pkg.render())
    if size <= budget:
        return pkg
    ev = pkg.evidence

    def line_no(e: Evidence) -> int:
        return int(e.id.removeprefix("readme:L"))

    readme = [e for e in ev if e.kind == "readme"]
    order = [
        *(e for e in reversed(ev) if e.kind == "file"),
        *(e for e in reversed(readme) if line_no(e) > README_HEAD_LINES),
        *(e for e in ev if e.kind == "commit"),
        *(e for e in reversed(readme) if line_no(e) <= README_HEAD_LINES),
    ]
    last_resort = [
        *(e for e in reversed(ev) if e.kind == "release"),
        *(e for e in ev if e.kind == "prev_post"),
    ]
    dropped: set[str] = set()

    def drop(candidates: Iterable[Evidence]) -> None:
        nonlocal size
        for e in candidates:
            if size <= budget:
                return
            dropped.add(e.id)
            size -= len(_entry(e)) + 1

    drop(order)
    tree = list(pkg.file_tree)
    while size > budget and tree:
        size -= len(_tree_line(tree.pop())) + 1
        if not tree:
            size -= len(TREE_HEADER) + 1
    drop(last_resort)
    if size > budget:
        raise ValueError("context budget is too small for the repository metadata")
    return pkg.model_copy(
        update={
            "evidence": [e for e in ev if e.id not in dropped],
            "file_tree": tree,
            "previous_post": None if "prev_post" in dropped else pkg.previous_post,
        }
    )


async def _fetch(
    gh: GitHubClient,
    full_name: str,
    head_sha: str | None,
    privacy_mode: PrivacyMode,
    commit_shas: Sequence[str],
    known: Mapping[str, GhCommit],
) -> tuple[str | None, list[GhRelease], list[str], list[GhCommit | None]]:
    async def readme() -> str | None:
        if privacy_mode == PrivacyMode.metadata_only:
            return None
        return await gh.get_readme(full_name, head_sha)

    async def tree() -> list[str]:
        return await gh.get_tree(full_name, head_sha) if head_sha else []

    async def one_commit(sha: str) -> GhCommit | None:
        if sha in known:  # already fetched for a manual range
            return known[sha]
        return await _commit_or_none(gh, full_name, sha)

    async def patches() -> list[GhCommit | None]:
        if privacy_mode != PrivacyMode.full:
            return []
        newest = commit_shas[-MAX_PATCH_COMMITS:]
        return list(await asyncio.gather(*(one_commit(s) for s in newest)))

    return await asyncio.gather(readme(), gh.list_releases(full_name), tree(), patches())


async def build_context(
    db: AsyncSession,
    gh: GitHubClient,
    event_id: uuid.UUID,
    privacy_mode: PrivacyMode,
    budget: int = CONTEXT_CHAR_BUDGET,
) -> ContextPackage:
    """Build (or reuse) the event's context package. Raises RedactionBlocked, LookupError."""
    if privacy_mode == PrivacyMode.never_send:
        raise RedactionBlocked("never_send")
    row = (
        await db.execute(
            select(
                Event.type,
                Event.base_sha,
                Event.head_sha,
                Event.stats,
                Event.context_hash,
                Event.context_json,
                Repo.id.label("repo_id"),
                Repo.full_name,
                Repo.visibility,
                Repo.default_branch,
                Repo.never_send_to_llm,
            )
            .join(Repo, Repo.id == Event.repo_id)
            .where(Event.id == event_id)
        )
    ).one_or_none()
    if row is None:
        raise LookupError("event not found")
    if row.never_send_to_llm:
        raise RedactionBlocked("never_send_to_llm")
    wanted_hash = context_hash(row.head_sha, privacy_mode)
    if row.context_hash == wanted_hash and row.context_json:
        return ContextPackage.model_validate(row.context_json)

    commits: Sequence[Any] = (
        await db.execute(
            select(Commit.sha, Commit.message, Commit.files)
            .where(Commit.event_id == event_id)
            .order_by(Commit.committed_at, Commit.sha)
        )
    ).all()
    prev_post = await db.scalar(
        select(Post.final_text)
        .join(Draft, Draft.id == Post.draft_id)
        .join(Event, Event.id == Draft.event_id)
        .where(Event.repo_id == row.repo_id)
        .order_by(Post.posted_at.desc())
        .limit(1)
    )
    await db.commit()  # no transaction stays open across the GitHub calls (DATABASE.md 2)

    fetched: dict[str, GhCommit] = {}
    if row.type == EventType.manual and row.head_sha:
        commits, fetched = await _range_commits(
            db, gh, row.repo_id, row.full_name, row.base_sha, row.head_sha
        )
    readme, releases, tree, patch_commits = await _fetch(
        gh, row.full_name, row.head_sha, privacy_mode, [c.sha for c in commits], fetched
    )

    clean = _Redactor()
    stats = row.stats or {}
    # Redact before cutting: a cut through a secret would leave a fragment redact() misses.
    stats_text = clean(json.dumps(stats, sort_keys=True, default=str))[:MAX_STATS_CHARS]
    meta = clean(
        f"repository: {row.full_name}\nvisibility: {row.visibility}\n"
        f"default branch: {row.default_branch}\nevent: {row.type}\n"
    )
    evidence = [Evidence(id="repo:meta", kind="repo", text=f"{meta}stats: {stats_text}")]
    # The owner's own post: redacted, but what fires there never blocks the repo.
    previous_post = _Redactor()(prev_post)[:MAX_PREV_POST_CHARS] if prev_post else None
    if previous_post:
        evidence.append(Evidence(id="prev_post", kind="prev_post", text=previous_post))
    event_tag = stats.get("tag") if isinstance(stats.get("tag"), str) else None
    evidence += _release_evidence(releases, event_tag, clean)
    if readme:
        evidence += _readme_evidence(readme, clean)
    evidence += _commit_evidence(commits, clean)
    evidence += _patch_evidence(patch_commits, clean)
    file_tree = [clean(p) for p in tree[:MAX_TREE_PATHS]]

    if clean.fired - _NON_BLOCKING_KINDS and row.visibility == RepoVisibility.private:
        raise RedactionBlocked("secret_in_private_repo")

    pkg = _fit(
        ContextPackage(
            repo_full_name=row.full_name,
            description=None,
            languages=[],
            topics=[],
            evidence=evidence,
            file_tree=file_tree,
            previous_post=previous_post,
            privacy_mode=privacy_mode,
        ),
        budget,
    )
    await db.execute(
        update(Event)
        .where(Event.id == event_id)
        .values(
            context_json=pkg.model_dump(mode="json"),
            context_hash=wanted_hash,
            context_built_at=func.now(),
        )
    )
    await db.commit()
    return pkg


async def build_rating_summary(pkg: ContextPackage, goals: str | None) -> str:
    """Compact, patch-free summary for the rating call (at most RATING_SUMMARY_LIMIT chars).

    Goals are the owner's text and sit outside the delimiters; repo text sits inside.
    """
    goals_text = _strip_delimiters((goals or "").strip())[:SUMMARY_GOALS_CHARS] or "(none)"
    head = f"Owner goals: {goals_text}\n"
    remaining = RATING_SUMMARY_LIMIT - len(head) - len(OPEN) - len(CLOSE) - 1
    entries: list[str] = []

    def add(candidates: Iterable[Evidence], section_cap: int) -> None:
        nonlocal remaining
        used = 0
        for e in candidates:
            text = _entry(e)[:SUMMARY_ENTRY_CHARS]
            cost = len(text) + 1
            if cost > remaining or used + cost > section_cap:
                continue
            entries.append(text)
            remaining -= cost
            used += cost

    ev = pkg.evidence
    add((e for e in ev if e.kind == "repo"), RATING_SUMMARY_LIMIT)
    add((e for e in ev if e.kind == "release"), SUMMARY_RELEASE_CHARS)
    add(
        (
            e
            for e in ev
            if e.kind == "readme" and int(e.id.removeprefix("readme:L")) <= SUMMARY_README_LINES
        ),
        SUMMARY_README_CHARS,
    )
    add((e for e in reversed(ev) if e.kind == "commit"), RATING_SUMMARY_LIMIT)
    return head + "\n".join([OPEN, *entries, CLOSE])
