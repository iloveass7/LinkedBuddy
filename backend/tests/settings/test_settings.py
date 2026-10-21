"""Settings API: full replace, bounds (SECURITY.md 4.1) and the 30 s in-process cache."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.db.models import SettingsRow
from linkedbuddy.security.auth import create_session
from linkedbuddy.settings import service

CSRF = {"X-Requested-With": "linkedbuddy"}


@pytest.fixture
async def api(client: AsyncClient, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    client.headers.update(CSRF)
    client.cookies.set("lb_session", await create_session(db))
    yield client


def full(**kw: Any) -> dict[str, Any]:
    return {
        "goals_text": "Grow a data-engineering audience",
        "default_tone": "casual",
        "custom_instructions": "Sound like a student who just shipped something.",
        "ban_list": ["synergy", "leverage"],
        "voice_samples": ["I built a thing.", "Another post."],
        "post_length": "short",
        "emoji_policy": "light",
        "hashtag_count": 2,
        "privacy_mode": "metadata_only",
    } | kw


async def test_settings_roundtrip_and_bounds(api: AsyncClient) -> None:
    put = await api.put("/api/settings", json=full())
    assert put.status_code == 200
    assert put.json() == full()
    got = await api.get("/api/settings")
    assert got.status_code == 200
    assert got.json() == full()

    bad = [
        {"voice_samples": ["x"] * 6},
        {"voice_samples": ["x" * 5001]},
        {"ban_list": ["x"] * 101},
        {"ban_list": ["x" * 81]},
        {"ban_list": [""]},
        {"goals_text": "x" * 2001},
        {"custom_instructions": "x" * 501},
        {"hashtag_count": 6},
        {"hashtag_count": -1},
        {"default_tone": "angry"},
        {"privacy_mode": "everything"},
        {"unknown": 1},
    ]
    for change in bad:
        resp = await api.put("/api/settings", json=full(**change))
        assert resp.status_code == 422, change
    # PUT is a full replace: a missing field is an error, not "keep the old value".
    partial = {k: v for k, v in full().items() if k != "privacy_mode"}
    assert (await api.put("/api/settings", json=partial)).status_code == 422
    assert (await api.get("/api/settings")).json() == full()


async def test_settings_require_owner(client: AsyncClient) -> None:
    client.headers.update(CSRF)
    assert (await client.get("/api/settings")).status_code == 401


async def test_settings_cache_ttl_and_invalidation(api: AsyncClient, db: AsyncSession) -> None:
    await api.put("/api/settings", json=full(goals_text="first"))
    assert (await service.get_cached_settings(db)).goals_text == "first"

    # A write that bypasses the API is not seen until the TTL runs out...
    await db.execute(update(SettingsRow).where(SettingsRow.id == 1).values(goals_text="direct"))
    await db.commit()
    assert (await service.get_cached_settings(db)).goals_text == "first"
    service._cache_expires_at = 0.0  # simulate the 30 s passing
    assert (await service.get_cached_settings(db)).goals_text == "direct"

    # ...while a PUT invalidates at once.
    await api.put("/api/settings", json=full(goals_text="second"))
    assert (await service.get_cached_settings(db)).goals_text == "second"
    assert service.SETTINGS_TTL_S == 30
