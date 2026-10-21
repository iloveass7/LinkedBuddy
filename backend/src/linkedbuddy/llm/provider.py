"""LLM provider interface and errors. No SDK imports here (ARCHITECTURE.md section 3)."""

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

Tier = Literal["fast", "quality"]


class LLMError(Exception):
    """Base class for LLM layer errors."""


class LLMRateLimited(LLMError):
    """The provider (or its 429) says to back off for `retry_after` seconds."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"LLM rate limited; retry after {retry_after:.1f}s")
        self.retry_after = retry_after


class LLMInvalidOutput(LLMError):
    """The model output did not validate against the schema after the retry."""

    def __init__(self, raw: str) -> None:
        # `raw` is kept for callers that want to store it; the message never includes it.
        super().__init__("LLM output failed schema validation")
        self.raw = raw


class LLMUnavailable(LLMError):
    """No API key is configured, so no LLM calls can be made."""


class LLMProvider(Protocol):
    async def generate_json(
        self,
        *,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
    ) -> T: ...


class UnavailableLLM:
    """Stand-in used when no API key is configured."""

    async def generate_json(
        self,
        *,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
    ) -> T:
        raise LLMUnavailable("GEMINI_API_KEY is not configured")
