"""Repo routes and the GitHub connection (mounted behind `require_owner`)."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.enums import RepoMode
from linkedbuddy.github.client import GitHubRateLimited
from linkedbuddy.github.ingest import GitHubNotConfigured
from linkedbuddy.repos import service

router = APIRouter(tags=["repos"])

Session = Annotated[AsyncSession, Depends(get_session)]

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{20,255}")
_SHA = Field(default=None, pattern=r"^[0-9a-fA-F]{4,64}$")


class GitHubTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Checked in the route: a pydantic constraint error would echo the token back as `input`.
    token: SecretStr


class RepoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    visibility: str
    mode: str
    never_send_to_llm: bool
    demo_url: str | None
    last_polled_at: datetime | None


class RepoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RepoMode | None = None
    demo_url: str | None = Field(default=None, max_length=500)  # null clears it
    never_send_to_llm: bool | None = None
    acknowledge_free_tier_data_use: bool = False

    @field_validator("demo_url")
    @classmethod
    def _https_only(cls, url: str | None) -> str | None:
        # Stored only, never fetched (SECURITY.md 4.4).
        if url is None:
            return None
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or any(ch.isspace() for ch in url):
            raise ValueError("demo_url must be an https:// URL")
        return url


class ManualEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_sha: str | None = _SHA
    head_sha: str | None = _SHA


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repo_id: uuid.UUID
    type: str
    status: str
    base_sha: str | None
    head_sha: str | None
    heuristic_score: float
    stats: dict[str, Any]
    created_at: datetime


class SyncOut(BaseModel):
    synced: int


class JobAccepted(BaseModel):
    job_id: uuid.UUID


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="repo not found")


@router.put("/connections/github", status_code=status.HTTP_204_NO_CONTENT)
async def put_github_token(body: GitHubTokenIn, request: Request, db: Session) -> None:
    token = body.token.get_secret_value()
    if not _TOKEN_RE.fullmatch(token):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="token must be 20-255 characters of A-Z, a-z, 0-9 or _",
        )
    await service.set_github_token(db, token, request.app.state.settings)


@router.post("/repos/sync", response_model=SyncOut)
async def sync_repos(request: Request, db: Session) -> SyncOut:
    try:
        synced = await service.sync(db, request.app.state.http, request.app.state.settings)
    except GitHubNotConfigured as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="github not connected") from exc
    except GitHubRateLimited as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="github rate limited"
        ) from exc
    return SyncOut(synced=synced)


@router.get("/repos", response_model=list[RepoOut])
async def list_repos(db: Session) -> list[RepoOut]:
    return [RepoOut.model_validate(row) for row in await service.list_repos(db)]


@router.patch("/repos/{repo_id}", response_model=RepoOut)
async def patch_repo(repo_id: uuid.UUID, body: RepoPatch, db: Session) -> RepoOut:
    changes = body.model_dump(
        include={"mode", "demo_url", "never_send_to_llm"} & body.model_fields_set
    )
    # Only demo_url may be cleared with null; a null mode or flag means "unchanged".
    changes = {k: v for k, v in changes.items() if v is not None or k == "demo_url"}
    try:
        repo = await service.update_repo(
            db, repo_id, changes, acknowledged=body.acknowledge_free_tier_data_use
        )
    except service.PrivateRepoNeedsAck as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="sending a private repo to Gemini needs acknowledge_free_tier_data_use: true",
        ) from exc
    if repo is None:
        raise _not_found()
    return RepoOut.model_validate(repo)


@router.post(
    "/repos/{repo_id}/poll", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def poll_repo(repo_id: uuid.UUID, db: Session) -> JobAccepted:
    job_id = await service.request_poll(db, repo_id)
    if job_id is None:
        raise _not_found()
    return JobAccepted(job_id=job_id)


@router.post(
    "/repos/{repo_id}/events", response_model=EventOut, status_code=status.HTTP_201_CREATED
)
async def create_event(repo_id: uuid.UUID, body: ManualEventIn, db: Session) -> EventOut:
    event = await service.create_manual_event(db, repo_id, body.base_sha, body.head_sha)
    if event is None:
        raise _not_found()
    return EventOut.model_validate(event)
