"""The draft output schemas and evidence validation (SECURITY.md 6.3, 6.4).

Every segment must cite evidence ids that exist in the event's ContextPackage. A segment with no
evidence, or with any unknown id, is marked unsupported and the UI flags it.
"""

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

MAX_SEGMENTS = 30
MAX_ALT_OPENINGS = 3
MIN_VARIANTS, MAX_VARIANTS = 2, 3
MERMAID_MAX_CHARS = 5000  # SECURITY.md 5.5; longer diagrams are dropped, not rendered

EvidenceId = Annotated[str, Field(max_length=300)]


class Segment(BaseModel):
    text: Annotated[str, Field(max_length=3000)]
    evidence: Annotated[list[EvidenceId], Field(max_length=10)]


class Variant(BaseModel):
    angle: Annotated[str, Field(max_length=300)]
    segments: Annotated[list[Segment], Field(min_length=1, max_length=MAX_SEGMENTS)]
    alt_openings: Annotated[
        list[Annotated[str, Field(max_length=500)]], Field(max_length=MAX_ALT_OPENINGS)
    ]
    first_comment: Annotated[str | None, Field(max_length=1000)] = None


class DraftResult(BaseModel):
    # No min/max_length here: Gemini rejects the request (400 INVALID_ARGUMENT) when this array
    # of objects carries minItems/maxItems. The count is checked below, after the model answers.
    variants: list[Variant]
    diagram_mermaid: str | None = None
    alt_text_diagram: Annotated[str | None, Field(max_length=1000)] = None

    @field_validator("variants")
    @classmethod
    def _variant_count(cls, value: list[Variant]) -> list[Variant]:
        if not MIN_VARIANTS <= len(value) <= MAX_VARIANTS:
            raise ValueError(f"expected {MIN_VARIANTS}-{MAX_VARIANTS} variants, got {len(value)}")
        return value


@dataclass(frozen=True)
class ValidatedDraft:
    body: str
    segments: list[dict[str, Any]]
    unsupported_claims: int


def join_segments(texts: list[str]) -> str:
    """Join with one space; a segment ending in a newline keeps its paragraph break instead."""
    out = ""
    for i, text in enumerate(texts):
        out += text
        if i < len(texts) - 1 and not text.endswith("\n"):
            out += " "
    return out.strip()


def validate_segments(segments: list[Segment], known_ids: set[str]) -> ValidatedDraft:
    checked = [
        {
            "text": s.text,
            "evidence": list(s.evidence),
            "supported": bool(s.evidence) and all(e in known_ids for e in s.evidence),
        }
        for s in segments
    ]
    return ValidatedDraft(
        body=join_segments([s.text for s in segments]),
        segments=checked,
        unsupported_claims=sum(not s["supported"] for s in checked),
    )
