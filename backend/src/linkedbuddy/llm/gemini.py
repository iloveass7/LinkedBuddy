"""Gemini provider. The only module that imports google.genai (ARCHITECTURE.md section 3)."""

import logging
import re
import time
from typing import Any

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from linkedbuddy.config import Settings
from linkedbuddy.llm.provider import (
    LLMInvalidOutput,
    LLMProvider,
    LLMRateLimited,
    T,
    Tier,
    UnavailableLLM,
)
from linkedbuddy.llm.ratelimit import TokenBucket

log = logging.getLogger(__name__)

DEFAULT_RETRY_AFTER = 10.0
_DELAY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")


def _retry_after(exc: errors.APIError) -> float:
    """Read `retryDelay` ("17s") from the RetryInfo entry of a 429 body; default 10 s."""
    details: Any = exc.details
    if isinstance(details, list) and len(details) == 1:
        details = details[0]
    err = details.get("error", details) if isinstance(details, dict) else {}
    entries = err.get("details") if isinstance(err, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and str(entry.get("@type", "")).endswith("RetryInfo"):
            match = _DELAY_RE.match(str(entry.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return DEFAULT_RETRY_AFTER


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        model_fast: str,
        model_quality: str,
        rpm_fast: int,
        rpm_quality: int,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._models: dict[Tier, str] = {"fast": model_fast, "quality": model_quality}
        self._buckets: dict[Tier, TokenBucket] = {
            "fast": TokenBucket(rpm_fast),
            "quality": TokenBucket(rpm_quality),
        }

    async def _call(self, tier: Tier, config: types.GenerateContentConfig, user: str) -> str:
        model = self._models[tier]
        await self._buckets[tier].acquire()
        started = time.monotonic()
        try:
            resp = await self._client.aio.models.generate_content(
                model=model, contents=user, config=config
            )
        except errors.ClientError as exc:
            if exc.code == 429:
                raise LLMRateLimited(_retry_after(exc)) from exc
            raise
        usage = resp.usage_metadata
        # Metadata only: never prompts or outputs (SECURITY.md section 7.1).
        log.info(
            "llm call model=%s tier=%s latency_ms=%d prompt_tokens=%s output_tokens=%s",
            model,
            tier,
            (time.monotonic() - started) * 1000,
            usage.prompt_token_count if usage else None,
            usage.candidates_token_count if usage else None,
        )
        return resp.text or ""

    async def generate_json(
        self,
        *,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
    ) -> T:
        # No `tools` are ever passed (SECURITY.md section 6.2).
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
        )
        raw = ""
        for _attempt in range(2):  # one retry on invalid output
            raw = await self._call(tier, config, user)
            try:
                return schema.model_validate_json(raw)
            except ValidationError:
                log.warning("llm invalid output tier=%s schema=%s", tier, schema.__name__)
        raise LLMInvalidOutput(raw)


def make_llm(settings: Settings) -> LLMProvider:
    """GeminiProvider when a key is configured, else a provider that raises LLMUnavailable."""
    api_key = settings.gemini_api_key.get_secret_value().strip() if settings.gemini_api_key else ""
    if not api_key:  # unset or blank (GEMINI_API_KEY=) must not crash startup
        return UnavailableLLM()
    return GeminiProvider(
        api_key=api_key,
        model_fast=settings.gemini_model_fast,
        model_quality=settings.gemini_model_quality,
        rpm_fast=settings.gemini_rpm_fast,
        rpm_quality=settings.gemini_rpm_quality,
    )
