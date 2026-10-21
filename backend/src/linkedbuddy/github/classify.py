"""Rule-based commit classifier. Pure: no I/O."""

import re
from dataclasses import dataclass
from typing import Literal

from linkedbuddy.github.client import GhCommit

Kind = Literal["merge", "dependency", "noise", "docs", "feature", "fix", "code"]

NOTEBOOK_DIVISOR = 10  # .ipynb lines count x0.1

_MERGE_MSG_RE = re.compile(r"^Merge (pull request|branch)")
_DEPENDENCY_MSG_RE = re.compile(r"^(Bump |chore\(deps\))")
_FEATURE_RE = re.compile(r"^feat(\(.+\))?!?:")
_FIX_RE = re.compile(r"^fix")
_IGNORED_RE = re.compile(
    r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock"
    r"|Cargo\.lock|go\.sum)$"
    r"|(^|/)(dist|build|\.next|node_modules)/"
    r"|\.min\.js$"
)
_DOCS_RE = re.compile(r"\.md$|(^|/)docs/")


@dataclass(frozen=True, slots=True)
class Classified:
    kind: Kind
    meaningful: int


def is_ignored(path: str) -> bool:
    """Lockfiles, build output and minified bundles: not the owner's work."""
    return _IGNORED_RE.search(path) is not None


def classify(c: GhCommit) -> Classified:
    message = c.message
    files = [f for f in c.files if not is_ignored(f.filename)]
    # Notebook lines are weighted x0.1 per file; summing then flooring once is exact.
    plain = sum(f.additions + f.deletions for f in files if not f.filename.endswith(".ipynb"))
    notebook = sum(f.additions + f.deletions for f in files if f.filename.endswith(".ipynb"))
    meaningful = plain + notebook // NOTEBOOK_DIVISOR

    if len(c.parents) > 1 or _MERGE_MSG_RE.match(message):
        return Classified("merge", meaningful)
    if (c.author_login or "").endswith("[bot]") or _DEPENDENCY_MSG_RE.match(message):
        return Classified("dependency", meaningful)
    if not files:
        return Classified("noise", 0)
    if all(_DOCS_RE.search(f.filename) for f in files):
        return Classified("docs", meaningful)
    if _FEATURE_RE.match(message):
        return Classified("feature", meaningful)
    if _FIX_RE.match(message):
        return Classified("fix", meaningful)
    return Classified("code", meaningful)
