import asyncio
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
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


@pytest.mark.parametrize(
    "bad",
    [
        "flowchart LR\nA[<img src=x>]-->B",
        "flowchart LR\nA[<IMG SRC=http:127.0.0.1:9>]-->B",
        'flowchart LR\nA["<a src=1>"]-->B',
        "flowchart LR\nA-->B\nstyle A fill:url(#x)",
        "flowchart LR\nA-->B\nclassDef c background:URL(x)",
        "%%{init: {'themeCSS': '.node{background:red}'}}%%\nflowchart LR\nA-->B",
        "flowchart LR\n%%{ init: {} }%%\nA-->B",
        "flowchart LR\nA-->B\n@import 'x'",
        "flowchart LR\nA[go to http:127.0.0.1]-->B",
        "flowchart LR\nA[HTTPS:example.com]-->B",
        "flowchart LR\nA[//evil.example/x]-->B",
        "---\nconfig:\n  theme: dark\n---\nflowchart LR\nA-->B",
        "  \n---\ntitle: x\n---\nflowchart LR\nA-->B",
    ],
)
def test_mermaid_rejects_network_and_injection_constructs(bad: str) -> None:
    check = check_mermaid(bad, TREE)
    assert not check.ok
    assert check.reason and check.reason in (
        "diagram contains a forbidden construct",
        "diagram contains an external reference or directive",
    )  # fixed strings: nothing from the source is echoed


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


def _procs_mentioning(*needles: str) -> list[int]:
    out: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:  # noqa: ASYNC230 - tiny proc read
                cmdline = f.read()
            if all(n.encode() in cmdline for n in needles):
                out.append(int(entry))
        except OSError:
            continue
    return out


BIG_FLOWCHART = "flowchart LR\n" + "\n".join(f"N{i} --> N{(i * 7 + 3) % 480}" for i in range(480))


@pytest.fixture
def private_tmp(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every temp dir and browser profile of the test lands under a path unique to it. It must
    stay short: Chromium's unix socket path limit is about 100 characters."""
    tmp = Path(tempfile.mkdtemp(prefix="lbt-"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setenv("TMPDIR", str(tmp))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


async def _assert_no_survivors(tmp: Path, *, seen_browser: bool) -> None:
    assert seen_browser, "the browser never started: the test would prove nothing"
    deadline = time.monotonic() + 5
    while _procs_mentioning(str(tmp)) and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.2)
    leftover = _procs_mentioning(str(tmp))
    for pid in leftover:  # do not leak into other tests even when failing
        os.kill(pid, 9)
    assert leftover == []


async def _run_and_watch(tmp: Path, task: "asyncio.Future[None]") -> bool:
    seen = False
    while not task.done():
        seen = seen or bool(_procs_mentioning("puppeteer_dev_chrome_profile", str(tmp)))
        await asyncio.sleep(0.05)
    return seen


@pytest.mark.skipif(not _mmdc_available(), reason="mmdc is not installed (see README)")
async def test_timeout_reaps_the_browser(
    monkeypatch: pytest.MonkeyPatch, private_tmp: Path
) -> None:
    monkeypatch.setattr(mermaid, "RENDER_TIMEOUT_S", 1.5)
    task = asyncio.ensure_future(render_mermaid(BIG_FLOWCHART, private_tmp / "o.png", _settings()))
    seen = await _run_and_watch(private_tmp, task)
    with pytest.raises(RenderFailed, match="timed out"):
        await task
    await _assert_no_survivors(private_tmp, seen_browser=seen)


@pytest.mark.skipif(not _mmdc_available(), reason="mmdc is not installed (see README)")
async def test_cancel_reaps_the_browser(private_tmp: Path) -> None:
    task = asyncio.ensure_future(render_mermaid(BIG_FLOWCHART, private_tmp / "o.png", _settings()))
    seen = False
    for _ in range(100):  # wait for the browser, then cancel mid-render
        if _procs_mentioning("puppeteer_dev_chrome_profile", str(private_tmp)):
            seen = True
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_no_survivors(private_tmp, seen_browser=seen)


class _Counter(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        _Counter.hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.mark.skipif(not _mmdc_available(), reason="mmdc is not installed (see README)")
@pytest.mark.parametrize(
    "template",
    [
        "flowchart LR\n  A[<img src=http:127.0.0.1:{port}/a.png>] --> B",
        "%%{{init: {{'themeCSS': '.node{{background:url(http://127.0.0.1:{port}/b.png)}}'}}}}%%\n"
        "flowchart LR\n  A[hello] --> B",
    ],
)
async def test_render_makes_no_network_requests(template: str, private_tmp: Path) -> None:
    _Counter.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Counter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        src = template.format(port=server.server_address[1])
        assert not check_mermaid(src, TREE).ok  # the static check rejects it as well
        try:
            # Straight to the renderer, bypassing check_mermaid: the browser itself must be
            # unable to reach the listener.
            await render_mermaid(src, private_tmp / "o.png", _settings())
        except RenderFailed:
            pass
        await asyncio.sleep(0.5)
    finally:
        server.shutdown()
        server.server_close()
    assert _Counter.hits == []


async def test_timeout_does_not_leave_output_written_during_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A renderer that writes its image from a SIGTERM handler must not leave it behind."""
    fake = tmp_path / "mmdc"
    fake.write_text(
        f"#!{sys.executable}\nimport signal, sys, time\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "def on_term(*_):\n"
        f"    open(out, 'wb').write({PNG!r})\n"
        "    raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM, on_term)\n"
        "time.sleep(60)\n"
    )
    fake.chmod(0o755)
    monkeypatch.setattr(mermaid, "RENDER_TIMEOUT_S", 1.0)
    settings = _settings().model_copy(update={"mmdc_path": str(fake)})
    out = tmp_path / "o.png"

    with pytest.raises(RenderFailed, match="timed out"):
        await render_mermaid(GOOD, out, settings)

    assert not out.exists()


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


class FailingSession:
    """sessionmaker stand-in: the `fail_on`-th session opened raises instead of working."""

    def __init__(self, real: Any, fail_on: int) -> None:
        self.real, self.fail_on, self.opened = real, fail_on, 0

    def __call__(self) -> Any:
        self.opened += 1
        if self.opened == self.fail_on:
            raise ConnectionError("db went away")
        return self.real()


def _with_sessions(ctx: JobContext, maker: Any) -> JobContext:
    return JobContext(maker, ctx.http, ctx.llm, ctx.settings)


async def test_db_failure_after_render_leaves_no_file_and_marks_failed(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    monkeypatch.setattr(mermaid, "render_mermaid", FakeRender())
    media_id = await seed_diagram(db)
    broken = _with_sessions(ctx, FailingSession(ctx.sessionmaker, fail_on=2))  # the store step

    with pytest.raises(ConnectionError):
        await render_diagram(broken, {"media_id": str(media_id)})

    assert not list(media_root.iterdir())  # the rendered file was removed
    media = await load(db, media_id)
    assert media.status == "failed" and media.meta["reason"] == "render error"
    assert media.file_path is None


async def test_unexpected_render_error_marks_failed_and_reraises(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch, media_root: Path
) -> None:
    async def boom(src: str, out: Path, settings: Settings | None = None) -> None:
        out.write_bytes(PNG)
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mermaid, "render_mermaid", boom)
    media_id = await seed_diagram(db)

    with pytest.raises(RuntimeError):
        await render_diagram(ctx, {"media_id": str(media_id)})

    media = await load(db, media_id)
    assert media.status == "failed" and media.meta["reason"] == "render error"
    assert not list(media_root.iterdir())


async def test_defer_does_not_mark_failed(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch
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


async def test_bad_stored_file_path_counts_as_not_rendered(
    db: AsyncSession, ctx: JobContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeRender()
    monkeypatch.setattr(mermaid, "render_mermaid", fake)
    media_id = await seed_diagram(db)
    media = await load(db, media_id)
    media.status, media.file_path, media.mime = "ready", "../../etc/passwd", "image/png"
    media.meta = {"source_sha256": mermaid.source_hash(GOOD)}
    await db.commit()

    assert await render_diagram(ctx, {"media_id": str(media_id)}) == {"status": "ready"}

    assert fake.calls == 1
    assert (await load(db, media_id)).file_path != "../../etc/passwd"


def test_puppeteer_config_is_generated_with_hardening(tmp_path: Path) -> None:
    import json

    configured = tmp_path / "pp.json"
    configured.write_text(json.dumps({"executablePath": "/x/chrome", "args": ["--no-sandbox"]}))
    settings = _settings().model_copy(update={"mmdc_puppeteer_config": configured})

    path = mermaid._write_puppeteer_config(settings, tmp_path)

    config = json.loads(path.read_text())
    assert config["executablePath"] == "/x/chrome"
    assert "--no-sandbox" not in config["args"]
    assert "--proxy-server=http://127.0.0.1:9" in config["args"]
    assert "--proxy-bypass-list=<-loopback>" in config["args"]


def test_puppeteer_config_without_a_configured_file(tmp_path: Path) -> None:
    import json

    settings = _settings().model_copy(update={"mmdc_puppeteer_config": tmp_path / "missing.json"})
    config = json.loads(mermaid._write_puppeteer_config(settings, tmp_path).read_text())
    assert config == {"args": mermaid._BROWSER_ARGS}
