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
