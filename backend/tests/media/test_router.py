"""Media routes over the rolled-back test DB, with MEDIA_ROOT in a temp dir."""

import io
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from linkedbuddy.config import Settings
from linkedbuddy.db.engine import get_session
from linkedbuddy.db.models import Draft, Event, Job, Media, Repo
from linkedbuddy.main import create_app
from linkedbuddy.media.router import MAX_MEDIA_PER_DRAFT
from linkedbuddy.security.auth import create_session

CSRF = {"X-Requested-With": "linkedbuddy"}


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "media"


@pytest.fixture
async def api(db: AsyncSession, settings: Settings, media_root: Path) -> AsyncIterator[AsyncClient]:
    app = create_app(settings.model_copy(update={"media_root": media_root}))

    async def _session() -> AsyncIterator[AsyncSession]:
        yield db

    app.dependency_overrides[get_session] = _session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=CSRF
    ) as c:
        c.cookies.set("lb_session", await create_session(db))
        yield c


def png_bytes(size: tuple[int, int] = (16, 16)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format="PNG")
    return out.getvalue()


def upload(content: bytes, name: str = "shot.png", mime: str = "image/png") -> dict[str, Any]:
    return {"files": {"file": (name, content, mime)}}


async def seed_draft(db: AsyncSession) -> uuid.UUID:
    repo = Repo(
        github_repo_id=uuid.uuid4().int % 10**12,
        full_name="octo/media",
        visibility="public",
        mode="always_suggest",
        default_branch="main",
        never_send_to_llm=False,
    )
    db.add(repo)
    await db.flush()
    event = Event(
        repo_id=repo.id,
        type="push_group",
        dedupe_key=f"t:{uuid.uuid4()}",
        head_sha="a" * 40,
        status="suggested",
    )
    db.add(event)
    await db.flush()
    draft = Draft(event_id=event.id, tone="technical", body="b", model="m", prompt_version="v")
    db.add(draft)
    await db.commit()
    return draft.id


async def seed_media(db: AsyncSession, draft_id: uuid.UUID, **values: Any) -> Media:
    fields: dict[str, Any] = {
        "type": "diagram",
        "source": "generated",
        "status": "pending",
        "mermaid_source": "flowchart LR\n  A --> B",
    }
    media = Media(draft_id=draft_id, **{**fields, **values})
    db.add(media)
    await db.commit()
    return media


async def test_upload_and_fetch_roundtrip(
    api: AsyncClient, db: AsyncSession, media_root: Path
) -> None:
    draft_id = await seed_draft(db)

    resp = await api.post(f"/api/drafts/{draft_id}/media", **upload(png_bytes()))

    assert resp.status_code == 201
    body = resp.json()
    assert body["type"] == "screenshot" and body["source"] == "uploaded"
    assert body["status"] == "ready" and body["position"] == 0 and body["included"] is True
    assert set(body) == {
        "id", "type", "source", "status", "alt_text", "position", "included", "mermaid_source",
    }  # fmt: skip
    stored = [p.name for p in media_root.iterdir()]
    assert len(stored) == 1 and stored[0] != "shot.png"  # server-generated name

    got = await api.get(f"/api/media/{body['id']}/file")
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"] == "inline"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert Image.open(io.BytesIO(got.content)).format == "PNG"


async def test_upload_ignores_claimed_type_and_name(api: AsyncClient, db: AsyncSession) -> None:
    draft_id = await seed_draft(db)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")

    resp = await api.post(
        f"/api/drafts/{draft_id}/media", **upload(buf.getvalue(), "../../x.svg", "image/svg+xml")
    )

    assert resp.status_code == 201
    got = await api.get(f"/api/media/{resp.json()['id']}/file")
    assert got.headers["content-type"] == "image/jpeg"


async def test_upload_rejects_non_image(
    api: AsyncClient, db: AsyncSession, media_root: Path
) -> None:
    draft_id = await seed_draft(db)

    resp = await api.post(f"/api/drafts/{draft_id}/media", **upload(b"%PDF-1.4", "a.png"))

    assert resp.status_code == 422
    assert resp.json() == {"detail": "file is not a valid image"}
    assert not media_root.exists() or not list(media_root.iterdir())
    assert (await db.scalars(select(Media))).all() == []


async def test_upload_over_10mb_is_413(api: AsyncClient, db: AsyncSession) -> None:
    draft_id = await seed_draft(db)
    resp = await api.post(
        f"/api/drafts/{draft_id}/media", **upload(b"\x00" * (10 * 1024 * 1024 + 1))
    )
    assert resp.status_code == 413


async def test_upload_unknown_draft_is_404(api: AsyncClient) -> None:
    resp = await api.post(f"/api/drafts/{uuid.uuid4()}/media", **upload(png_bytes()))
    assert resp.status_code == 404


async def test_upload_positions_increment_and_tray_is_capped(
    api: AsyncClient, db: AsyncSession
) -> None:
    draft_id = await seed_draft(db)
    await seed_media(db, draft_id, position=4)

    first = await api.post(f"/api/drafts/{draft_id}/media", **upload(png_bytes()))
    assert first.json()["position"] == 5

    for _ in range(MAX_MEDIA_PER_DRAFT - 2):
        assert (
            await api.post(f"/api/drafts/{draft_id}/media", **upload(png_bytes()))
        ).status_code == 201
    full = await api.post(f"/api/drafts/{draft_id}/media", **upload(png_bytes()))
    assert full.status_code == 409


async def test_patch_media(api: AsyncClient, db: AsyncSession) -> None:
    media = await seed_media(db, await seed_draft(db))

    resp = await api.patch(
        f"/api/media/{media.id}", json={"alt_text": "A flow", "position": 3, "included": False}
    )

    assert resp.status_code == 200
    assert resp.json()["alt_text"] == "A flow"
    assert resp.json()["position"] == 3 and resp.json()["included"] is False
    cleared = await api.patch(f"/api/media/{media.id}", json={"alt_text": None})
    assert cleared.json()["alt_text"] is None and cleared.json()["position"] == 3


@pytest.mark.parametrize(
    "body",
    [{"alt_text": "x" * 1001}, {"position": 21}, {"position": -1}, {"nope": 1}, {"included": "x"}],
)
async def test_patch_media_validation(
    api: AsyncClient, db: AsyncSession, body: dict[str, Any]
) -> None:
    media = await seed_media(db, await seed_draft(db))
    assert (await api.patch(f"/api/media/{media.id}", json=body)).status_code == 422


async def test_patch_unknown_media_is_404(api: AsyncClient) -> None:
    assert (await api.patch(f"/api/media/{uuid.uuid4()}", json={})).status_code == 404


async def test_put_mermaid_queues_render(api: AsyncClient, db: AsyncSession) -> None:
    media = await seed_media(db, await seed_draft(db), status="failed", meta={"reason": "boom"})

    resp = await api.put(f"/api/media/{media.id}/mermaid", json={"source": "flowchart LR\n X-->Y"})

    assert resp.status_code == 202
    job = await db.get(Job, uuid.UUID(resp.json()["job_id"]))
    assert job and job.kind == "render_diagram" and job.dedupe_key == f"diagram:{media.id}"
    assert job.payload == {"media_id": str(media.id)}
    await db.refresh(media)
    assert media.mermaid_source == "flowchart LR\n X-->Y" and media.status == "pending"
    assert "reason" not in media.meta

    again = await api.put(f"/api/media/{media.id}/mermaid", json={"source": "flowchart LR\n P-->Q"})
    assert again.status_code == 202 and again.json() == resp.json()  # live job is reused


async def test_put_while_render_running_queues_a_follow_up(
    api: AsyncClient, db: AsyncSession
) -> None:
    media = await seed_media(db, await seed_draft(db))
    running = Job(
        kind="render_diagram",
        payload={"media_id": str(media.id)},
        status="running",
        dedupe_key=f"diagram:{media.id}",
    )
    db.add(running)
    await db.commit()

    resp = await api.put(f"/api/media/{media.id}/mermaid", json={"source": "flowchart LR\n X-->Y"})

    assert resp.status_code == 202
    follow_up = await db.get(Job, uuid.UUID(resp.json()["job_id"]))
    assert follow_up and follow_up.id != running.id and follow_up.status == "queued"
    assert follow_up.kind == "render_diagram"
    assert follow_up.dedupe_key == f"diagram:{media.id}:next"
    assert follow_up.payload == {"media_id": str(media.id)}

    again = await api.put(f"/api/media/{media.id}/mermaid", json={"source": "flowchart LR\n P-->Q"})
    assert again.json() == resp.json()  # one follow-up is enough: it reads the latest source


async def test_upload_cleans_up_when_the_write_fails_midway(
    api: AsyncClient, db: AsyncSession, media_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from linkedbuddy.media import router as media_router

    real_write = media_router._write

    def write_then_fail(path: Path, data: bytes) -> None:
        real_write(path, data)
        raise OSError("disk went away")

    monkeypatch.setattr(media_router, "_write", write_then_fail)
    draft_id = await seed_draft(db)

    with pytest.raises(OSError):
        await api.post(f"/api/drafts/{draft_id}/media", **upload(png_bytes()))

    assert not list(media_root.iterdir())


async def test_put_mermaid_rejects_non_diagram_and_bad_body(
    api: AsyncClient, db: AsyncSession
) -> None:
    draft_id = await seed_draft(db)
    shot = await seed_media(db, draft_id, type="screenshot", source="uploaded", mermaid_source=None)
    diagram = await seed_media(db, draft_id)

    assert (await api.put(f"/api/media/{shot.id}/mermaid", json={"source": "x"})).status_code == 409
    for body in ({"source": ""}, {"source": "x" * 5001}, {}):
        assert (await api.put(f"/api/media/{diagram.id}/mermaid", json=body)).status_code == 422
    assert (
        await api.put(f"/api/media/{uuid.uuid4()}/mermaid", json={"source": "x"})
    ).status_code == 404


async def test_file_404_unless_ready_and_present(
    api: AsyncClient, db: AsyncSession, media_root: Path
) -> None:
    draft_id = await seed_draft(db)
    media_root.mkdir()
    (media_root / "a.png").write_bytes(png_bytes())
    pending = await seed_media(db, draft_id, file_path="a.png", mime="image/png")
    missing = await seed_media(db, draft_id, status="ready", file_path="gone.png", mime="image/png")
    escape = await seed_media(db, draft_id, status="ready", file_path="../x.png", mime="image/png")
    odd_mime = await seed_media(
        db, draft_id, status="ready", file_path="a.png", mime="image/svg+xml"
    )
    no_file = await seed_media(db, draft_id, status="ready")

    for m in (pending, missing, escape, odd_mime, no_file):
        assert (await api.get(f"/api/media/{m.id}/file")).status_code == 404
    assert (await api.get(f"/api/media/{uuid.uuid4()}/file")).status_code == 404

    pending.status = "ready"
    await db.commit()
    assert (await api.get(f"/api/media/{pending.id}/file")).status_code == 200


async def test_media_routes_require_login(client: AsyncClient) -> None:
    assert (await client.get(f"/api/media/{uuid.uuid4()}/file")).status_code == 401
    resp = await client.post(
        f"/api/drafts/{uuid.uuid4()}/media", headers=CSRF, **upload(png_bytes())
    )
    assert resp.status_code == 401
