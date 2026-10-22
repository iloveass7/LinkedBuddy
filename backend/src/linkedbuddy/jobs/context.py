"""What a job handler receives. Kept apart from `handlers` so handler modules can import it."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.llm.provider import LLMProvider

# Long renders: own worker process and a longer stale-lock window (scheduler).
RENDER_VIDEO = "render_video"


@dataclass(frozen=True)
class JobContext:
    """Shared per-process resources. Handlers open one short session per DB step.

    `deferrals` is per job: how many times the running job was deferred before (the worker sets
    it on a copy for each run). Handlers pass it to `queue.llm_backoff`.
    """

    sessionmaker: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    llm: LLMProvider
    settings: Settings
    deferrals: int = 0


Handler = Callable[[JobContext, dict[str, Any]], Awaitable[dict[str, Any]]]
