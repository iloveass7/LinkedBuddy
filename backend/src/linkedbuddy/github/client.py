"""Read-only GitHub REST client. Issues GET requests only (SECURITY.md §1.6)."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

BASE_URL = "https://api.github.com"
LOW_REMAINING = 100
MAX_PAGES = 50
MAX_TREE_PATHS = 5000

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")


class GitHubRateLimited(Exception):
    def __init__(self, reset_at: datetime) -> None:
        super().__init__(f"GitHub rate limited until {reset_at.isoformat()}")
        self.reset_at = reset_at


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class GhRepo(_Frozen):
    id: int
    full_name: str
    default_branch: str | None = None
    private: bool = False
    fork: bool = False
    archived: bool = False
    description: str | None = None
    created_at: datetime | None = None
    pushed_at: datetime | None = None


class GhCommitRef(_Frozen):
    sha: str
    committed_at: datetime | None = None


class CommitsPage(_Frozen):
    not_modified: bool
    etag: str | None
    items: list[GhCommitRef]


class GhFile(_Frozen):
    filename: str
    additions: int = 0
    deletions: int = 0
    status: str = "modified"
    # Diff text: held in memory for the context builder only, never dumped (SECURITY.md 6.9).
    patch: str | None = Field(default=None, exclude=True)


class GhCommit(_Frozen):
    sha: str
    parents: list[str]
    author_login: str | None
    message: str
    files: list[GhFile]
    committed_at: datetime | None


class GhRelease(_Frozen):
    tag_name: str
    name: str | None = None
    body: str | None = None
    draft: bool = False
    prerelease: bool = False
    published_at: datetime | None = None


def _repo(full_name: str) -> str:
    if not _REPO_RE.match(full_name) or ".." in full_name:
        raise ValueError("invalid repository name")
    return full_name


def _sha(value: str) -> str:
    if not _SHA_RE.match(value):
        raise ValueError("invalid commit sha")
    return value


def _check_rate_limit(resp: httpx.Response) -> None:
    now = datetime.now(UTC)
    retry_after = resp.headers.get("retry-after")
    if resp.status_code in (403, 429) and retry_after is not None:
        delay = int(retry_after) if retry_after.isdigit() else 60
        raise GitHubRateLimited(now + timedelta(seconds=delay))
    remaining = resp.headers.get("x-ratelimit-remaining")
    if remaining is not None and remaining.isdigit() and int(remaining) < LOW_REMAINING:
        reset = resp.headers.get("x-ratelimit-reset", "")
        reset_at = (
            datetime.fromtimestamp(int(reset), UTC)
            if reset.isdigit()
            else now + timedelta(minutes=15)
        )
        raise GitHubRateLimited(reset_at)


class GitHubClient:
    def __init__(self, http: httpx.AsyncClient, token: str) -> None:
        self._http = http
        self._token = token

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        ok_statuses: tuple[int, ...] = (),
    ) -> httpx.Response:
        """GET with auth; raises GitHubRateLimited / HTTPStatusError. 2xx and ok_statuses pass."""
        all_headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **(headers or {}),
        }
        resp = await self._http.get(url, params=params, headers=all_headers)
        _check_rate_limit(resp)
        if resp.status_code not in ok_statuses:
            resp.raise_for_status()
        return resp

    async def list_repos(self) -> list[GhRepo]:
        repos: list[GhRepo] = []
        url = f"{BASE_URL}/user/repos"
        params: dict[str, Any] | None = {"per_page": 100, "affiliation": "owner"}
        for _ in range(MAX_PAGES):
            resp = await self._get(url, params)
            repos.extend(GhRepo.model_validate(r) for r in resp.json())
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                break
            url = next_url
            params = None  # the next URL already carries its query string
        return repos

    async def list_commits(
        self, full_name: str, branch: str, since: datetime | None, etag: str | None
    ) -> CommitsPage:
        params: dict[str, Any] = {"sha": branch, "per_page": 100}
        if since is not None:
            params["since"] = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        headers = {"If-None-Match": etag} if etag else None
        resp = await self._get(
            f"{BASE_URL}/repos/{_repo(full_name)}/commits", params, headers, ok_statuses=(304, 409)
        )
        if resp.status_code == 304:
            return CommitsPage(not_modified=True, etag=etag, items=[])
        if resp.status_code == 409:  # empty repository
            return CommitsPage(not_modified=False, etag=None, items=[])
        items = [
            GhCommitRef(
                sha=c["sha"],
                committed_at=((c.get("commit") or {}).get("committer") or {}).get("date"),
            )
            for c in resp.json()
        ]
        return CommitsPage(not_modified=False, etag=resp.headers.get("etag"), items=items)

    async def get_commit(self, full_name: str, sha: str) -> GhCommit:
        resp = await self._get(f"{BASE_URL}/repos/{_repo(full_name)}/commits/{_sha(sha)}")
        data = resp.json()
        commit = data.get("commit") or {}
        who = commit.get("committer") or commit.get("author") or {}
        return GhCommit(
            sha=data["sha"],
            parents=[p["sha"] for p in data.get("parents", [])],
            author_login=(data.get("author") or {}).get("login"),
            message=commit.get("message", ""),
            files=[GhFile.model_validate(f) for f in data.get("files", [])],
            committed_at=who.get("date"),
        )

    async def compare(self, full_name: str, base: str, head: str) -> list[str] | None:
        resp = await self._get(
            f"{BASE_URL}/repos/{_repo(full_name)}/compare/{_sha(base)}...{_sha(head)}",
            {"per_page": 100},
            ok_statuses=(404,),
        )
        if resp.status_code == 404:  # base no longer exists (force-push)
            return None
        return [c["sha"] for c in resp.json().get("commits", [])]

    async def get_readme(self, full_name: str, ref: str | None = None) -> str | None:
        resp = await self._get(
            f"{BASE_URL}/repos/{_repo(full_name)}/readme",
            {"ref": _sha(ref)} if ref else None,
            headers={"Accept": "application/vnd.github.raw+json"},
            ok_statuses=(404,),
        )
        return None if resp.status_code == 404 else resp.text

    async def list_releases(self, full_name: str) -> list[GhRelease]:
        resp = await self._get(f"{BASE_URL}/repos/{_repo(full_name)}/releases", {"per_page": 30})
        return [GhRelease.model_validate(r) for r in resp.json()]

    async def get_tree(self, full_name: str, sha: str) -> list[str]:
        resp = await self._get(
            f"{BASE_URL}/repos/{_repo(full_name)}/git/trees/{_sha(sha)}", {"recursive": "1"}
        )
        paths = [e["path"] for e in resp.json().get("tree", []) if e.get("type") == "blob"]
        return paths[:MAX_TREE_PATHS]
