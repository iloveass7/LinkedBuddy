import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from linkedbuddy.config import Settings
from linkedbuddy.db.models import Draft, Event, Media, Repo
from linkedbuddy.http import make_http_client
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer
from linkedbuddy.llm.fake import FakeLLM
from linkedbuddy.media import mermaid
from linkedbuddy.media.mermaid import RenderFailed, check_mermaid, render_diagram, render_mermaid

TREE = ["src/real.py", "src/pkg/util.py", "README.md"]
GOOD = "flowchart LR\n  A[src/real.py] --> B[Reranker]"
PNG = mermaid.PNG_MAGIC + b"fake"


def _settings(media_root: Path | None = None) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@localhost:5432/x",
        app_encryption_key="k",  # type: ignore[arg-type]
        owner_password_hash="h",  # type: ignore[arg-type]
        **({"media_root": media_root} if media_root else {}),
    )


def _mmdc_available() -> bool:
    exe = _settings().mmdc_executable
    return Path(exe).is_file() or shutil.which(exe) is not None


# --- check_mermaid -----------------------------------------------------------------------------


def test_mermaid_accepts_known_paths_and_directories() -> None:
    assert check_mermaid(GOOD, TREE).ok
    assert check_mermaid('flowchart LR\n  A["src/pkg/"] --> B(src/pkg)\n  C{README.md}', TREE).ok


def test_mermaid_without_paths_is_fine() -> None:
    assert check_mermaid("flowchart LR\n  query --> reranker --> answer", []).ok


def test_mermaid_rejects_click() -> None:
    check = check_mermaid("flowchart LR\nA-->B\nclick A href 'javascript:x'", TREE)
    assert not check.ok and check.reason


@pytest.mark.parametrize(
    "bad",
    [
        "flowchart LR\nA-->B\nCLICK A callback",
        "flowchart LR\nA-->B\nA --- HREF",
        "flowchart LR\nA[<script>x</script>]-->B",
        "flowchart LR\nA[<ScRiPt>]-->B",
        "flowchart LR\nA-->B\nlink JavaScript:alert(1)",
    ],
)
def test_mermaid_rejects_forbidden_constructs(bad: str) -> None:
    assert not check_mermaid(bad, TREE).ok


def test_mermaid_rejects_empty_and_too_long() -> None:
    assert not check_mermaid("  \n", TREE).ok
    assert check_mermaid("flowchart LR\n" + "A-->B\n" * 1000, TREE).reason.startswith(  # type: ignore[union-attr]
        "diagram is longer"
    )


def test_mermaid_hallucinated_path() -> None:
    check = check_mermaid("flowchart LR\n  A[src/nope.py] --> B", ["src/real.py"])
    assert not check.ok
    assert check.reason and "src/nope.py" in check.reason


def test_mermaid_hallucinated_bare_filename_and_directory() -> None:
    assert not check_mermaid("flowchart LR\n  A[ghost.py] --> B", TREE).ok
    assert not check_mermaid("flowchart LR\n  A[lib/] --> B", TREE).ok
    assert not check_mermaid('flowchart LR\n  A["see src/real.py and src/gone.ts"] --> B', TREE).ok


def test_mermaid_html_breaks_are_not_paths() -> None:
    assert check_mermaid("flowchart LR\n  A[Parse<br/>input] --> B", TREE).ok


# --- render_mermaid ----------------------------------------------------------------------------


async def test_render_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_exec = asyncio.create_subprocess_exec
    started: list[Any] = []

    async def sleeper(*args: Any, **kwargs: Any) -> Any:
        proc = await real_exec("sleep", "60", **kwargs)
        started.append(proc)
        return proc

    monkeypatch.setattr(mermaid.asyncio, "create_subprocess_exec", sleeper)
    monkeypatch.setattr(mermaid, "RENDER_TIMEOUT_S", 0.2)
    out = tmp_path / "out.png"
    with pytest.raises(RenderFailed, match="timed out"):
        await render_mermaid(GOOD, out, _settings())
    assert started[0].returncode is not None  # killed and reaped
    assert not out.exists()


async def test_render_failure_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    real_exec = asyncio.create_subprocess_exec

    async def failing(*args: Any, **kwargs: Any) -> Any:
        return await real_exec("false", **kwargs)

    monkeypatch.setattr(mermaid.asyncio, "create_subprocess_exec", failing)
    with pytest.raises(RenderFailed):
        await render_mermaid(GOOD, tmp_path / "out.png", _settings())


async def test_render_uses_argument_list_and_clean_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_exec = asyncio.create_subprocess_exec
    seen: dict[str, Any] = {}

    async def spy(*args: Any, **kwargs: Any) -> Any:
        seen.update(args=args, kwargs=kwargs)
        # Stand-in "mmdc": writes a PNG to the path after -o.
        out = args[args.index("-o") + 1]
        return await real_exec("sh", "-c", f"printf '\\211PNG\\r\\n\\032\\n' > '{out}'", **kwargs)

    monkeypatch.setenv("GEMINI_API_KEY", "secret-should-not-leak")
    monkeypatch.setenv("PUPPETEER_EXECUTABLE_PATH", "/x/chrome")
    monkeypatch.setattr(mermaid.asyncio, "create_subprocess_exec", spy)
    out = tmp_path / "out.png"
    await render_mermaid(GOOD, out, _settings())

    args, kwargs = seen["args"], seen["kwargs"]
    assert "--no-sandbox" not in args and all(isinstance(a, str) for a in args)
    assert args[args.index("-c") + 1].endswith("mermaid-config.json")
    assert set(kwargs["env"]) <= {"PATH", "HOME", "TMPDIR", "PUPPETEER_EXECUTABLE_PATH"}
    assert "shell" not in kwargs
    assert out.read_bytes().startswith(mermaid.PNG_MAGIC)


@pytest.mark.skipif(not _mmdc_available(), reason="mmdc is not installed (see README)")
async def test_render_mermaid_real(tmp_path: Path) -> None:
    out = tmp_path / "out.png"
    await render_mermaid("flowchart LR\n  A[Query] --> B[Answer]", out, _settings())
    assert out.stat().st_size > 0
    assert out.read_bytes().startswith(mermaid.PNG_MAGIC)


# --- render_diagram job ------------------------------------------------------------------------


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    return tmp_path / "media"


@pytest.fixture
async def ctx(db: AsyncSession, media_root: Path) -> AsyncIterator[JobContext]:
    conn = await db.connection()
    maker = async_sessionmaker(
        bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    async with make_http_client() as http:
        yield JobContext(maker, http, FakeLLM({}), _settings(media_root))


class FakeRender:
    """Stands in for mmdc: writes a PNG-looking file (or raises) and counts calls."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls = 0
        self.fail = fail

    async def __call__(self, src: str, out: Path, settings: Settings | None = None) -> None:
        self.calls += 1
        if self.fail:
            raise RenderFailed(self.fail)
        out.write_bytes(PNG)


async def seed_diagram(
    db: AsyncSession, src: str = GOOD, tree: list[str] | None = None
) -> uuid.UUID:
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
        context_json={"file_tree": TREE if tree is None else tree},
    )
    db.add(event)
    await db.flush()
    draft = Draft(event_id=event.id, tone="technical", body="b", model="m", prompt_version="v")
    db.add(draft)
    await db.flush()
    media = Media(
        draft_id=draft.id,
        type="diagram",
        source="generated",
        status="pending",
        mermaid_source=src,
    )
    db.add(media)
    await db.commit()
    return media.id


async def load(db: AsyncSession, media_id: uuid.UUID) -> Media:
    db.expire_all()
    return (await db.scalars(select(Media).where(Media.id == media_id))).one()


def test_handler_registered() -> None:
    assert HANDLERS["render_diagram"] is render_diagram


async def test_render_diagram_success(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    fake = FakeRender()
    monkeypatch.setattr(mermaid, "render_mermaid", fake)
    media_id = await seed_diagram(db)

    result = await render_diagram(ctx, {"media_id": str(media_id)})

    media = await load(db, media_id)
    assert result == {"status": "ready"}
    assert media.status == "ready" and media.mime == "image/png"
    assert media.meta["source_sha256"] == mermaid.source_hash(GOOD)
    assert media.file_path and (media_root / media.file_path).read_bytes() == PNG
    assert uuid.UUID(media.file_path.removesuffix(".png"))  # server-generated name


async def test_render_diagram_is_idempotent(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRender()
    monkeypatch.setattr(mermaid, "render_mermaid", fake)
    media_id = await seed_diagram(db)
    await render_diagram(ctx, {"media_id": str(media_id)})
    first = (await load(db, media_id)).file_path

    assert await render_diagram(ctx, {"media_id": str(media_id)}) == {"skipped": "already_rendered"}

    assert fake.calls == 1 and (await load(db, media_id)).file_path == first


async def test_rerender_replaces_and_deletes_old_file(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    monkeypatch.setattr(mermaid, "render_mermaid", FakeRender())
    media_id = await seed_diagram(db)
    await render_diagram(ctx, {"media_id": str(media_id)})
    old = (await load(db, media_id)).file_path
    assert old

    media = await load(db, media_id)
    media.mermaid_source = "flowchart LR\n  X --> Y"
    media.status = "pending"
    await db.commit()
    await render_diagram(ctx, {"media_id": str(media_id)})

    media = await load(db, media_id)
    assert media.status == "ready" and media.file_path and media.file_path != old
    assert not (media_root / old).exists()
    assert (media_root / media.file_path).exists()
    assert media.meta["source_sha256"] == mermaid.source_hash("flowchart LR\n  X --> Y")


async def test_failed_check_records_reason_and_does_not_render(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRender()
    monkeypatch.setattr(mermaid, "render_mermaid", fake)
    media_id = await seed_diagram(db, "flowchart LR\n  A[src/ghost.py] --> B")

    result = await render_diagram(ctx, {"media_id": str(media_id)})

    media = await load(db, media_id)
    assert result["status"] == "failed" and fake.calls == 0
    assert media.status == "failed" and "src/ghost.py" in media.meta["reason"]


async def test_render_failure_records_reason(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    monkeypatch.setattr(mermaid, "render_mermaid", FakeRender(fail="rendering timed out"))
    media_id = await seed_diagram(db)

    await render_diagram(ctx, {"media_id": str(media_id)})

    media = await load(db, media_id)
    assert media.status == "failed" and media.meta["reason"] == "rendering timed out"
    assert media.file_path is None
    assert not media_root.exists() or not list(media_root.iterdir())


async def test_edit_during_render_defers_and_drops_output(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    media_id = await seed_diagram(db)

    async def render_then_edit(src: str, out: Path, settings: Settings | None = None) -> None:
        out.write_bytes(PNG)
        media = await load(db, media_id)
        media.mermaid_source = "flowchart LR\n  NEW --> SRC"
        await db.commit()

    monkeypatch.setattr(mermaid, "render_mermaid", render_then_edit)
    with pytest.raises(Defer):
        await render_diagram(ctx, {"media_id": str(media_id)})

    assert (await load(db, media_id)).status == "pending"
    assert not list(media_root.iterdir())  # the stale render was removed


async def test_missing_media_is_skipped(ctx: JobContext) -> None:
    assert await render_diagram(ctx, {"media_id": str(uuid.uuid4())}) == {
        "skipped": "media not found"
    }
