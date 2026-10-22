"""All ORM models (DATABASE.md section 6). The schema is one unit; Alembic imports this module.

Enum-like columns are ``text`` + ``CHECK``, mirrored by the StrEnums in ``linkedbuddy.enums``.
Every relationship is ``lazy="raise"``: load with ``selectinload()`` or a join.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from linkedbuddy.enums import (
    ConnectionProvider,
    DecisionAction,
    DraftStatus,
    EmojiPolicy,
    EventStatus,
    EventType,
    JobStatus,
    MediaSource,
    MediaStatus,
    MediaType,
    PostLength,
    PrivacyMode,
    PublishMethod,
    Rating,
    RepoMode,
    RepoVisibility,
    Tone,
)

__all__ = [
    "AuthSession",
    "Base",
    "Commit",
    "Connection",
    "ConnectionProvider",
    "Decision",
    "DecisionAction",
    "Draft",
    "DraftStatus",
    "EmojiPolicy",
    "Event",
    "EventStatus",
    "EventType",
    "Job",
    "JobStatus",
    "Media",
    "MediaSource",
    "MediaStatus",
    "MediaType",
    "Post",
    "PostLength",
    "PrivacyMode",
    "PublishMethod",
    "Rating",
    "Repo",
    "RepoMode",
    "RepoVisibility",
    "Series",
    "SettingsRow",
    "Tone",
]

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _in_check(column: str, enum: type[Any], name: str) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _jsonb(default: str, *, nullable: bool = False) -> Mapped[Any]:
    if nullable:
        return mapped_column(JSONB, nullable=True)
    return mapped_column(JSONB, server_default=text(f"'{default}'::jsonb"))


class AuthSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        UniqueConstraint("provider", name="uq_connections_provider"),
        _in_check("provider", ConnectionProvider, "provider"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(Text)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    member_urn: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class SettingsRow(Base):
    """Singleton owner settings (``id = 1``)."""

    __tablename__ = "settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        _in_check("default_tone", Tone, "default_tone"),
        _in_check("post_length", PostLength, "post_length"),
        _in_check("emoji_policy", EmojiPolicy, "emoji_policy"),
        CheckConstraint("hashtag_count BETWEEN 0 AND 5", name="hashtag_count"),
        _in_check("privacy_mode", PrivacyMode, "privacy_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    goals_text: Mapped[str] = mapped_column(Text, server_default=text("''"))
    default_tone: Mapped[str] = mapped_column(Text, server_default=text("'professional'"))
    custom_instructions: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ban_list: Mapped[list[str]] = _jsonb("[]")
    voice_samples: Mapped[list[str]] = _jsonb("[]")
    post_length: Mapped[str] = mapped_column(Text, server_default=text("'medium'"))
    emoji_policy: Mapped[str] = mapped_column(Text, server_default=text("'none'"))
    hashtag_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    privacy_mode: Mapped[str] = mapped_column(Text, server_default=text("'metadata_plus_readme'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = (
        UniqueConstraint("github_repo_id", name="uq_repos_github_repo_id"),
        _in_check("visibility", RepoVisibility, "visibility"),
        _in_check("mode", RepoMode, "mode"),
        Index(
            "ix_repos_mode",
            "mode",
            postgresql_where=text("mode <> 'muted'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    github_repo_id: Mapped[int] = mapped_column(BigInteger)
    full_name: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str] = mapped_column(Text)
    demo_url: Mapped[str | None] = mapped_column(Text)
    never_send_to_llm: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    last_seen_sha: Mapped[str | None] = mapped_column(Text)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    events: Mapped[list["Event"]] = relationship(
        back_populates="repo", lazy="raise", passive_deletes=True
    )
    commits: Mapped[list["Commit"]] = relationship(
        back_populates="repo", lazy="raise", passive_deletes=True
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_events_dedupe_key"),
        _in_check("type", EventType, "type"),
        _in_check("rating", Rating, "rating"),
        _in_check("status", EventStatus, "status"),
        Index("ix_events_status_rating_created_at", "status", "rating", text("created_at DESC")),
        Index("ix_events_repo_id_created_at", "repo_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    base_sha: Mapped[str | None] = mapped_column(Text)
    head_sha: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = _jsonb("{}")
    heuristic_score: Mapped[float] = mapped_column(REAL, server_default=text("0"))
    rating: Mapped[str | None] = mapped_column(Text)
    rating_reason: Mapped[str | None] = mapped_column(Text)
    rating_json: Mapped[dict[str, Any] | None] = _jsonb("{}", nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    context_json: Mapped[dict[str, Any] | None] = _jsonb("{}", nullable=True)
    context_hash: Mapped[str | None] = mapped_column(Text)
    context_built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    repo: Mapped[Repo] = relationship(back_populates="events", lazy="raise")
    commits: Mapped[list["Commit"]] = relationship(back_populates="event", lazy="raise")
    drafts: Mapped[list["Draft"]] = relationship(
        back_populates="event", lazy="raise", passive_deletes=True
    )
    decisions: Mapped[list["Decision"]] = relationship(
        back_populates="event", lazy="raise", passive_deletes=True
    )


class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        ForeignKeyConstraint(["repo_id"], ["repos.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        Index("ix_commits_event_id", "event_id"),
    )

    repo_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    sha: Mapped[str] = mapped_column(Text, primary_key=True)
    event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    classification: Mapped[str] = mapped_column(Text)
    meaningful_lines: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    author_login: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    files: Mapped[list[Any]] = _jsonb("[]")
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    repo: Mapped[Repo] = relationship(back_populates="commits", lazy="raise")
    event: Mapped[Event | None] = relationship(back_populates="commits", lazy="raise")


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (Index("ix_series_repo_id", "repo_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("repos.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text)

    repo: Mapped[Repo | None] = relationship(lazy="raise")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="series", lazy="raise")


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = (
        _in_check("tone", Tone, "tone"),
        _in_check("status", DraftStatus, "status"),
        Index("ix_drafts_event_id_created_at", "event_id", text("created_at DESC")),
        Index("ix_drafts_parent_draft_id", "parent_draft_id"),
        Index("ix_drafts_series_id", "series_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    parent_draft_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("drafts.id", ondelete="SET NULL")
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("series.id", ondelete="SET NULL")
    )
    variant_index: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    angle: Mapped[str] = mapped_column(Text, server_default=text("''"))
    tone: Mapped[str] = mapped_column(Text)
    instructions_used: Mapped[str] = mapped_column(Text, server_default=text("''"))
    body: Mapped[str] = mapped_column(Text)
    segments: Mapped[list[Any]] = _jsonb("[]")
    alt_openings: Mapped[list[Any]] = _jsonb("[]")
    first_comment: Mapped[str | None] = mapped_column(Text)
    specificity_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    unsupported_claims: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    lint: Mapped[dict[str, Any]] = _jsonb("{}")
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    created_at: Mapped[datetime] = _created_at()

    event: Mapped[Event] = relationship(back_populates="drafts", lazy="raise")
    series: Mapped[Series | None] = relationship(back_populates="drafts", lazy="raise")
    parent: Mapped["Draft | None"] = relationship(
        remote_side="Draft.id", back_populates="children", lazy="raise"
    )
    children: Mapped[list["Draft"]] = relationship(back_populates="parent", lazy="raise")
    media: Mapped[list["Media"]] = relationship(
        back_populates="draft", lazy="raise", passive_deletes=True
    )
    posts: Mapped[list["Post"]] = relationship(
        back_populates="draft", lazy="raise", passive_deletes=True
    )


class Media(Base):
    __tablename__ = "media"
    __table_args__ = (
        _in_check("type", MediaType, "type"),
        _in_check("source", MediaSource, "source"),
        _in_check("status", MediaStatus, "status"),
        Index("ix_media_draft_id_position", "draft_id", "position"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)  # relative to MEDIA_ROOT
    mime: Mapped[str | None] = mapped_column(Text)
    mermaid_source: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    included: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    meta: Mapped[dict[str, Any]] = _jsonb("{}")

    draft: Mapped[Draft] = relationship(back_populates="media", lazy="raise")


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        _in_check("publish_method", PublishMethod, "publish_method"),
        Index("ix_posts_draft_id", "draft_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    draft_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    publish_method: Mapped[str] = mapped_column(Text)
    linkedin_post_urn: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str] = mapped_column(Text)
    covered_base_sha: Mapped[str | None] = mapped_column(Text)
    covered_head_sha: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = _jsonb("{}")

    draft: Mapped[Draft] = relationship(back_populates="posts", lazy="raise")


class Decision(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        _in_check("action", DecisionAction, "action"),
        _in_check("rating_at_time", Rating, "rating_at_time"),
        Index("ix_decisions_event_id", "event_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(Text)
    rating_at_time: Mapped[str | None] = mapped_column(Text)
    heuristic_at_time: Mapped[float | None] = mapped_column(REAL)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped[Event] = relationship(back_populates="decisions", lazy="raise")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        _in_check("status", JobStatus, "status"),
        Index(
            "ix_jobs_kind_run_after",
            "kind",
            "run_after",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "uq_jobs_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = _jsonb("{}")
    status: Mapped[str] = mapped_column(Text, server_default=text("'queued'"))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = _jsonb("{}", nullable=True)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
