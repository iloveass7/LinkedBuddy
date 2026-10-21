"""Scripted LLM for tests: queue responses per schema class, inspect recorded calls."""

from dataclasses import dataclass

from pydantic import BaseModel

from linkedbuddy.llm.provider import T, Tier


@dataclass(frozen=True)
class FakeCall:
    tier: Tier
    system: str
    user: str
    schema: type[BaseModel]
    temperature: float


class FakeLLM:
    def __init__(self, responses: dict[type[BaseModel], list[BaseModel | Exception]]) -> None:
        self._responses = {schema: list(items) for schema, items in responses.items()}
        self.calls: list[FakeCall] = []

    async def generate_json(
        self,
        *,
        tier: Tier,
        system: str,
        user: str,
        schema: type[T],
        temperature: float,
    ) -> T:
        self.calls.append(FakeCall(tier, system, user, schema, temperature))
        queue = self._responses.get(schema)
        if not queue:
            raise AssertionError(f"FakeLLM has no queued response for {schema.__name__}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, schema):
            raise AssertionError(f"FakeLLM queued {type(item).__name__}, wanted {schema.__name__}")
        return item
