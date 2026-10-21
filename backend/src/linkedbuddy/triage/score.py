"""Free heuristic first stage of triage. Pure: no I/O."""

import math
from dataclasses import dataclass

from linkedbuddy.enums import EventType, RepoMode

HEURISTIC_GATE = 2.0
LARGE_CHANGE_LINES = 300

_LOW_VALUE_KINDS = frozenset({"merge", "dependency", "noise", "docs"})
_ALWAYS_PASS_TYPES = frozenset(
    {EventType.new_repo, EventType.release, EventType.manual, EventType.labeled}
)


@dataclass(frozen=True, slots=True)
class EventSignals:
    type: EventType
    meaningful: int
    new_files: int
    has_feat: bool
    readme_changed: bool
    labeled: bool
    kinds: list[str]


def heuristic_score(s: EventSignals) -> float:
    if s.kinds and all(k in _LOW_VALUE_KINDS for k in s.kinds):
        return 0.0
    score = 0.0
    if s.type is EventType.new_repo:
        score += 5
    if s.type is EventType.release:
        score += 4
    if s.labeled:
        score += 5
    if s.has_feat:
        score += 2
    score += min(s.new_files, 3)
    if s.readme_changed:
        score += 1
    score += min(3.0, math.log10(s.meaningful + 1))
    return score


def passes_mode(mode: RepoMode, s: EventSignals, score: float) -> bool:
    if mode is RepoMode.muted:
        return False
    if mode is RepoMode.manual_only:
        return s.type is EventType.manual
    if mode is RepoMode.releases_and_large:
        return s.type in _ALWAYS_PASS_TYPES or s.meaningful >= LARGE_CHANGE_LINES
    return score >= HEURISTIC_GATE or s.type in _ALWAYS_PASS_TYPES
