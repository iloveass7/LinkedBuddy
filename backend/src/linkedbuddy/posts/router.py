"""Post routes (mounted behind `require_owner`). Copy-paste only: nothing is sent to LinkedIn."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.engine import get_session
from linkedbuddy.drafts.lint import LINKEDIN_MAX_CHARS
from linkedbuddy.posts import service

router = APIRouter(tags=["posts"])

Session = Annotated[AsyncSession, Depends(get_session)]
MAX_METRIC = 10**9
Metric = Annotated[int, Field(strict=True, ge=0, le=MAX_METRIC)]


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    final_text: Annotated[str, Field(min_length=1, max_length=LINKEDIN_MAX_CHARS)]
    method: Literal["manual"]  # API publishing is a later plan

    @field_validator("final_text")
    @classmethod
    def _publishable(cls, text: str) -> str:
        # Fixed messages: the text is the owner's draft and is never echoed back.
        if not text.strip():
            raise ValueError("final_text is blank")
        if "[REDACTED" in text:
            raise ValueError("final_text still contains a redaction marker")
        return text


class MetricsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    impressions: Metric | None = None
    reactions: Metric | None = None
    comments: Metric | None = None
    reposts: Metric | None = None
    saves: Metric | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "MetricsIn":
        if not self.model_fields_set:
            raise ValueError("at least one metric is required")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("metrics cannot be null")  # None only means "not provided"
        return self


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    posted_at: datetime
    publish_method: str
    final_text: str
    covered_base_sha: str | None
    covered_head_sha: str | None
    metrics: dict[str, Any]


class PostsOut(BaseModel):
    items: list[PostOut]
    next_cursor: str | None


@router.post(
    "/drafts/{draft_id}/publish", response_model=PostOut, status_code=status.HTTP_201_CREATED
)
async def publish_draft(draft_id: uuid.UUID, body: PublishIn, db: Session) -> PostOut:
    try:
        post = await service.publish(db, draft_id, body.final_text)
    except service.DraftNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="draft not found") from exc
    except service.DraftNotApproved as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="draft is not approved") from exc
    return PostOut.model_validate(post)


@router.patch("/posts/{post_id}/metrics", response_model=PostOut)
async def patch_metrics(post_id: uuid.UUID, body: MetricsIn, db: Session) -> PostOut:
    try:
        post = await service.update_metrics(db, post_id, body.model_dump(exclude_unset=True))
    except service.PostNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="post not found") from exc
    return PostOut.model_validate(post)


@router.get("/repos/{repo_id}/posts", response_model=PostsOut)
async def list_repo_posts(
    repo_id: uuid.UUID,
    db: Session,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PostsOut:
    try:
        page = await service.list_posts(db, repo_id, cursor=cursor, limit=limit)
    except service.RepoNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="repo not found") from exc
    except service.InvalidCursor as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid cursor") from exc
    return PostsOut(
        items=[PostOut.model_validate(r) for r in page.items], next_cursor=page.next_cursor
    )
