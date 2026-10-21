"""Shared enums used by the triage logic and the DB models."""

from enum import StrEnum


class RepoMode(StrEnum):
    always_suggest = "always_suggest"
    releases_and_large = "releases_and_large"
    manual_only = "manual_only"
    muted = "muted"


class EventType(StrEnum):
    new_repo = "new_repo"
    release = "release"
    pr_merged = "pr_merged"
    push_group = "push_group"
    manual = "manual"
    labeled = "labeled"


class EventStatus(StrEnum):
    new = "new"
    suggested = "suggested"
    generating = "generating"
    drafted = "drafted"
    dismissed = "dismissed"
    posted = "posted"


class Rating(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class DraftStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    discarded = "discarded"
    posted = "posted"


class MediaType(StrEnum):
    screenshot = "screenshot"
    diagram = "diagram"
    chart = "chart"
    pdf = "pdf"
    video = "video"


class MediaSource(StrEnum):
    uploaded = "uploaded"
    generated = "generated"
    repo = "repo"


class MediaStatus(StrEnum):
    pending = "pending"
    ready = "ready"
    failed = "failed"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


class Tone(StrEnum):
    professional = "professional"
    casual = "casual"
    technical = "technical"
    storytelling = "storytelling"


class PostLength(StrEnum):
    short = "short"
    medium = "medium"
    long = "long"


class PrivacyMode(StrEnum):
    full = "full"
    metadata_plus_readme = "metadata_plus_readme"
    metadata_only = "metadata_only"
    never_send = "never_send"


class EmojiPolicy(StrEnum):
    none = "none"
    light = "light"
    any = "any"


class ConnectionProvider(StrEnum):
    github = "github"
    linkedin = "linkedin"


class PublishMethod(StrEnum):
    manual = "manual"
    api = "api"


class DecisionAction(StrEnum):
    generate = "generate"
    ignore = "ignore"
    post = "post"
    discard = "discard"


class RepoVisibility(StrEnum):
    public = "public"
    private = "private"
