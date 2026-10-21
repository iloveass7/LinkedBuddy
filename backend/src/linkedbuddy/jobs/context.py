"""What a job handler receives. Kept apart from `handlers` so handler modules can import it."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.llm.provider import LLMProvider


@dataclass(frozen=True)
class JobContext:
    """Shared per-process resources. Handlers open one short session per DB step."""

    sessionmaker: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    llm: LLMProvider
    settings: Settings


Handler = Callable[[JobContext, dict[str, Any]], Awaitable[dict[str, Any]]]
