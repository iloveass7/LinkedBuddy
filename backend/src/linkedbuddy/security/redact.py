"""Outbound redaction: strips secrets and emails from text before any LLM call."""

import re
from dataclasses import dataclass


class RedactionBlocked(Exception):  # noqa: N818 - name fixed by SECURITY.md
    """Redaction fired on content that must not leave the machine (private repo)."""


@dataclass(frozen=True)
class RedactResult:
    text: str
    kinds: list[str]


_ENV_KEY = r"[A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)[A-Za-z0-9_]*"

# Order matters: private keys first (they may contain anything), env lines after
# the specific token kinds so a known token keeps its more precise marker.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        # No END line (e.g. truncated at the context budget): redact to end of string.
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----"
            r"(?:.*?-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|.*\Z)",
            re.S,
        ),
    ),
    (
        "github_token",
        re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    ),
    ("aws_key", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("google_key", re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]+")),
    (
        "jwt",
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ),
    (
        "env_secret",
        re.compile(
            rf"(?<![A-Za-z0-9_]){_ENV_KEY}[ \t]*={{1,2}}[ \t]*"
            r"(?!\[REDACTED:)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s=]\S*)",
            re.I,
        ),
    ),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
]


def redact(text: str) -> RedactResult:
    kinds: list[str] = []
    for kind, pattern in _RULES:
        text, count = pattern.subn(f"[REDACTED:{kind}]", text)
        if count:
            kinds.append(kind)
    return RedactResult(text=text, kinds=kinds)
