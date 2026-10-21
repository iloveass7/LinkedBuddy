"""The video script (what Gemini writes), its limits, and the renderer interface.

Only this module's types cross the boundary to a concrete renderer (see `video.brag`).
"""

import uuid
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema, model_validator

MIN_SCENES = 3
MAX_SCENES = 12
MAX_SCENE_TEXT = 280
MIN_SCENE_S = 1.0
MAX_SCENE_S = 15.0
MAX_TOTAL_S = 90.0


def _uuid_or_none(value: object) -> uuid.UUID | None:
    """The model may write "", "none" or "diagram-1": anything that is not a UUID is no reference
    (the scene stays; failing the whole script would only burn another quality-tier call)."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


# Gemini's schema subset has no `uuid` string format, so the model sees a plain nullable string.
MediaRef = Annotated[
    uuid.UUID | None,
    BeforeValidator(_uuid_or_none),
    WithJsonSchema({"anyOf": [{"type": "string"}, {"type": "null"}]}),
]


class Scene(BaseModel):
    kind: Literal["title", "diagram", "screenshot", "code", "stats", "text"]
    text: str = Field(max_length=MAX_SCENE_TEXT)
    media_id: MediaRef = None
    duration_s: float = Field(ge=MIN_SCENE_S, le=MAX_SCENE_S)


class VideoScript(BaseModel):
    title: str = Field(max_length=100)
    scenes: list[Scene] = Field(min_length=MIN_SCENES, max_length=MAX_SCENES)

    @model_validator(mode="after")
    def _total_duration(self) -> Self:
        if sum(s.duration_s for s in self.scenes) > MAX_TOTAL_S:
            raise ValueError(f"total duration is over {MAX_TOTAL_S:g} seconds")
        return self


class RenderFailed(Exception):
    """The renderer timed out, crashed or produced no video. The message is safe to show."""


class VideoRenderer(Protocol):
    async def render(self, script: VideoScript, assets: dict[uuid.UUID, Path], out: Path) -> None:
        """Write an MP4 to `out`. Raises RenderFailed; never leaves a partial `out`."""
        ...
