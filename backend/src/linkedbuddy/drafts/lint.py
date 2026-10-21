"""Post-generation lint: banned phrases, buzzwords, specificity and the LinkedIn length limit."""

import re
from dataclasses import asdict, dataclass
from typing import Any

LINKEDIN_MAX_CHARS = 3000

# Always applied, merged with the owner's list (spec 7.6).
BUILTIN_BAN = ["excited to announce", "game-changer", "thrilled", "delighted to share"]

BUZZWORDS = (
    "leverage",
    "synergy",
    "cutting-edge",
    "game-changing",
    "revolutionary",
    "disruptive",
    "seamless",
    "seamlessly",
    "innovative",
    "next-level",
    "world-class",
    "best-in-class",
    "paradigm",
    "supercharge",
    "empower",
    "unlock",
    "robust",
)
SPECIFIC_WORDS = ("because", "instead of", "fixed", "switched", "tradeoff")

_BUZZ_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, BUZZWORDS)) + r")\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_FILE_RE = re.compile(r"\b\w+\.(?:py|ts|js|go|rs|ipynb|md)\b")
_BACKTICK_RE = re.compile(r"`[^`\n]+`")
_WORDS_RE = re.compile(r"\b(?:" + "|".join(map(re.escape, SPECIFIC_WORDS)) + r")\b", re.IGNORECASE)


@dataclass(frozen=True)
class LintReport:
    banned_hits: list[str]
    buzzword_count: int
    specificity_count: int
    char_count: int
    over_limit: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def ban_phrases(*lists: list[str]) -> list[str]:
    """Lower-cased, de-duplicated, blank entries dropped, first occurrence order kept."""
    seen: dict[str, None] = {}
    for phrases in lists:
        for p in phrases:
            if p.strip():
                seen.setdefault(p.strip().lower(), None)
    return list(seen)


def specificity_count(body: str) -> int:
    return sum(len(rx.findall(body)) for rx in (_NUMBER_RE, _FILE_RE, _BACKTICK_RE, _WORDS_RE))


def lint(body: str, ban_list: list[str]) -> LintReport:
    lowered = body.lower()
    return LintReport(
        banned_hits=[p for p in ban_phrases(ban_list) if p in lowered],
        buzzword_count=len(_BUZZ_RE.findall(body)),
        specificity_count=specificity_count(body),
        char_count=len(body),
        over_limit=len(body) > LINKEDIN_MAX_CHARS,
    )
