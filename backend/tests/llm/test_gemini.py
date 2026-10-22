import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai import errors, types
from pydantic import BaseModel, SecretStr

from linkedbuddy.config import Settings
from linkedbuddy.llm.fake import FakeLLM
from linkedbuddy.llm.gemini import GeminiProvider, make_llm
from linkedbuddy.llm.provider import (
    LLMInvalidOutput,
    LLMRateLimited,
    LLMUnavailable,
    Tier,
    UnavailableLLM,
)


class Model(BaseModel):
    x: int


class _Usage:
    prompt_token_count = 11
    candidates_token_count = 7


class _Resp:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.usage_metadata = _Usage()


def _provider(*responses: Any) -> tuple[GeminiProvider, AsyncMock]:
    p = GeminiProvider("fake-key", "m-fast", "m-quality", rpm_fast=100, rpm_quality=100)
    mock = AsyncMock(side_effect=list(responses))
    p._client.aio.models.generate_content = mock  # type: ignore[method-assign]
    return p, mock


async def _gen(p: GeminiProvider, tier: Tier = "fast") -> Model:
    return await p.generate_json(tier=tier, system="sys", user="usr", schema=Model, temperature=0.3)


async def test_gemini_parses_schema() -> None:
    p, mock = _provider(_Resp('{"x":1}'))
    assert await _gen(p) == Model(x=1)
    kwargs = mock.call_args.kwargs
    assert kwargs["model"] == "m-fast"
    assert kwargs["contents"] == "usr"
    config: types.GenerateContentConfig = kwargs["config"]
    assert config.system_instruction == "sys"
    assert config.response_mime_type == "application/json"
    assert config.response_schema is Model
    assert config.temperature == 0.3


async def test_gemini_uses_model_for_tier() -> None:
    p, mock = _provider(_Resp('{"x":1}'))
    await _gen(p, "quality")
    assert mock.call_args.kwargs["model"] == "m-quality"


async def test_gemini_retries_once_on_invalid() -> None:
    p, mock = _provider(_Resp("not json"), _Resp('{"x":2}'))
    assert await _gen(p) == Model(x=2)
    assert mock.await_count == 2


async def test_gemini_invalid_twice_raises_invalid_output() -> None:
    p, mock = _provider(_Resp("nope"), _Resp('{"x":"str"}'))
    with pytest.raises(LLMInvalidOutput) as ei:
        await _gen(p)
    assert ei.value.raw == '{"x":"str"}'
    assert mock.await_count == 2


async def test_gemini_empty_text_counts_as_invalid() -> None:
    p, _ = _provider(_Resp(None), _Resp(None))
    with pytest.raises(LLMInvalidOutput):
        await _gen(p)


def _429(details: Any) -> errors.ClientError:
    return errors.ClientError(429, details)


async def test_gemini_429_maps_to_rate_limited_with_retry_info() -> None:
    body = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"},
            ],
        }
    }
    p, _ = _provider(_429(body))
    with pytest.raises(LLMRateLimited) as ei:
        await _gen(p)
    assert ei.value.retry_after == 17.0


async def test_gemini_429_defaults_to_10s_without_retry_info() -> None:
    p, _ = _provider(_429({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}))
    with pytest.raises(LLMRateLimited) as ei:
        await _gen(p)
    assert ei.value.retry_after == 10.0


@pytest.mark.parametrize("code", [500, 503, 504])
async def test_gemini_overloaded_maps_to_rate_limited(code: int) -> None:
    # "This model is currently experiencing high demand": wait it out, not a failed attempt.
    p, _ = _provider(errors.ServerError(code, {"error": {"code": code, "status": "UNAVAILABLE"}}))
    with pytest.raises(LLMRateLimited) as ei:
        await _gen(p, "quality")
    assert ei.value.retry_after == 10.0


async def test_gemini_other_server_error_propagates() -> None:
    p, _ = _provider(errors.ServerError(501, {"error": {"code": 501}}))
    with pytest.raises(errors.ServerError):
        await _gen(p)


async def test_gemini_non_429_client_error_propagates() -> None:
    p, _ = _provider(errors.ClientError(400, {"error": {"code": 400}}))
    with pytest.raises(errors.ClientError):
        await _gen(p)


async def test_gemini_passes_no_tools() -> None:
    p, mock = _provider(_Resp('{"x":1}'))
    await _gen(p)
    assert mock.call_args.kwargs["config"].tools is None


async def test_gemini_logs_metadata_only(caplog: pytest.LogCaptureFixture) -> None:
    p, _ = _provider(_Resp('{"x":1}'))
    with caplog.at_level(logging.INFO, logger="linkedbuddy.llm.gemini"):
        await _gen(p)
    text = caplog.text
    assert "model=m-fast" in text and "tier=fast" in text
    assert "prompt_tokens=11" in text and "output_tokens=7" in text
    assert "usr" not in text and '"x"' not in text


def _settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost/db",
        app_encryption_key="k",  # type: ignore[arg-type]
        owner_password_hash="h",  # type: ignore[arg-type]
        **kw,
    )


async def test_make_llm_without_key_raises_unavailable() -> None:
    llm = make_llm(_settings())
    with pytest.raises(LLMUnavailable):
        await llm.generate_json(tier="fast", system="s", user="u", schema=Model, temperature=0.1)


def test_make_llm_with_key_builds_gemini() -> None:
    assert isinstance(make_llm(_settings(gemini_api_key=SecretStr("fake"))), GeminiProvider)


async def test_fake_llm_queues_per_schema_and_records_calls() -> None:
    boom = LLMRateLimited(5.0)
    fake = FakeLLM({Model: [Model(x=1), boom]})
    kw: dict[str, Any] = {"tier": "fast", "system": "s", "user": "u", "temperature": 0.1}
    assert await fake.generate_json(schema=Model, **kw) == Model(x=1)
    with pytest.raises(LLMRateLimited):
        await fake.generate_json(schema=Model, **kw)
    assert [(c.tier, c.system, c.user, c.schema) for c in fake.calls] == [
        ("fast", "s", "u", Model)
    ] * 2
    with pytest.raises(AssertionError, match="Model"):
        await fake.generate_json(schema=Model, **kw)


async def test_app_lifespan_sets_llm_state() -> None:
    from linkedbuddy.main import create_app

    app = create_app(_settings())
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.llm, UnavailableLLM)


@pytest.mark.parametrize("blank", ["", "   "])
async def test_make_llm_blank_key_is_unavailable(blank: str) -> None:
    llm = make_llm(_settings(gemini_api_key=SecretStr(blank)))
    assert isinstance(llm, UnavailableLLM)
    with pytest.raises(LLMUnavailable):
        await llm.generate_json(tier="fast", system="s", user="u", schema=Model, temperature=0.1)
