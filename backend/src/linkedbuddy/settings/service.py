"""Owner settings: the singleton `settings` row, its API schemas and a 30 s in-process cache.

The cache (ARCHITECTURE.md 8) is per process. A PUT through this process invalidates it at once;
other processes (the worker) see the change within SETTINGS_TTL_S.
"""

import time
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import SettingsRow
from linkedbuddy.enums import EmojiPolicy, PostLength, PrivacyMode, Tone

SETTINGS_TTL_S = 30

BanPhrase = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
VoiceSample = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
]


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    goals_text: str
    default_tone: Tone
    custom_instructions: str
    ban_list: list[str]
    voice_samples: list[str]
    post_length: PostLength
    emoji_policy: EmojiPolicy
    hashtag_count: int
    privacy_mode: PrivacyMode


class SettingsIn(BaseModel):
    """A full replacement: every field is required (SECURITY.md 4.1 bounds)."""

    model_config = ConfigDict(extra="forbid")

    goals_text: Annotated[str, Field(max_length=2000)]
    default_tone: Tone
    custom_instructions: Annotated[str, Field(max_length=500)]
    ban_list: Annotated[list[BanPhrase], Field(max_length=100)]
    voice_samples: Annotated[list[VoiceSample], Field(max_length=5)]
    post_length: PostLength
    emoji_policy: EmojiPolicy
    hashtag_count: Annotated[int, Field(ge=0, le=5)]
    privacy_mode: PrivacyMode


DEFAULTS = SettingsOut(
    goals_text="",
    default_tone=Tone.professional,
    custom_instructions="",
    ban_list=[],
    voice_samples=[],
    post_length=PostLength.medium,
    emoji_policy=EmojiPolicy.none,
    hashtag_count=0,
    privacy_mode=PrivacyMode.metadata_plus_readme,
)

_cache: SettingsOut | None = None
_cache_expires_at = 0.0


def clear_settings_cache() -> None:
    global _cache, _cache_expires_at
    _cache, _cache_expires_at = None, 0.0


async def get_settings_row(db: AsyncSession) -> SettingsOut:
    """Read the row (one query). The migration seeds it; defaults cover a missing row."""
    row = await db.scalar(select(SettingsRow).where(SettingsRow.id == 1))
    return SettingsOut.model_validate(row) if row is not None else DEFAULTS


async def get_cached_settings(db: AsyncSession) -> SettingsOut:
    """The settings, at most SETTINGS_TTL_S old."""
    global _cache, _cache_expires_at
    now = time.monotonic()
    if _cache is None or now >= _cache_expires_at:
        _cache = await get_settings_row(db)
        _cache_expires_at = now + SETTINGS_TTL_S
    return _cache


async def replace_settings(db: AsyncSession, new: SettingsIn) -> SettingsOut:
    """Overwrite every field (upsert of the singleton). Commits and invalidates the cache."""
    values = new.model_dump(mode="json")
    stmt = pg_insert(SettingsRow).values(id=1, **values)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[SettingsRow.id], set_={**values, "updated_at": func.now()}
        )
    )
    await db.commit()
    clear_settings_cache()
    return SettingsOut.model_validate(values)
