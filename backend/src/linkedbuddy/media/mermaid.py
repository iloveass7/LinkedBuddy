"""Mermaid diagrams: static checks, the `mmdc` render, and the `render_diagram` job.

Source from the model or the owner is untrusted. It is length-capped, scanned for interactive or
script constructs, and every file path named in a node label must exist in the repo's file tree
(a diagram must not show files that are not there). Rendering runs `mmdc` as an argument list in a
private temp dir, with a 30 s timeout, Mermaid `securityLevel: strict`, the browser sandbox ON
(SECURITY.md 5.5), and an environment without secrets.
"""

import asyncio
import hashlib
import os
import re
import shutil
import signal
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from linkedbuddy.config import Settings, get_settings
from linkedbuddy.db.models import Draft, Event, Media
from linkedbuddy.drafts.validate import MERMAID_MAX_CHARS
from linkedbuddy.enums import MediaStatus, MediaType
from linkedbuddy.jobs.context import JobContext
from linkedbuddy.jobs.handlers import HANDLERS
from linkedbuddy.jobs.queue import Defer
from linkedbuddy.media.storage import media_path, new_media_name

RENDER_TIMEOUT_S = 30.0
RENDER_WIDTH_PX = 1600
MMDC_CONFIG = Path(__file__).resolve().parents[3] / "mermaid-config.json"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Case-insensitive. `click`/`href` are Mermaid interaction directives: whole words only, so a
# label like "Clickstream" is fine; the other two are never legitimate, so any occurrence fails.
_FORBIDDEN_WORDS = re.compile(r"\b(?:click|href)\b", re.IGNORECASE)
_FORBIDDEN_SUBSTRINGS = ("<script", "javascript:")

PATH_EXTENSIONS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".ipynb", ".md",
    ".json", ".yaml", ".yml", ".toml", ".sql", ".c", ".cpp", ".h",
)  # fmt: skip

# Label heuristic: the text inside [...], (...), {...} and "..." (shapes like [(db)] or ((x)) just
# yield their inner text plus punctuation, which is stripped). Labels are split on whitespace
# after removing HTML tags (<br/>); a token is a path if it contains "/" or ends in a known
# source extension. Edge labels (|text|) and bare node ids are deliberately not inspected.
_LABEL_RES = tuple(
    re.compile(p) for p in (r"\[([^\]]*)\]", r"\(([^)]*)\)", r"\{([^}]*)\}", r'"([^"]*)"')
)
_HTML_TAG = re.compile(r"<[^>]*>")
_EDGE_PUNCT = "\"'`()[]{}<>,;:!?"


class RenderFailed(Exception):
    """`mmdc` timed out, crashed or produced no image. The message is safe to show."""


@dataclass(frozen=True)
class MermaidCheck:
    ok: bool
    reason: str | None = None


def _path_tokens(src: str) -> list[str]:
    tokens: list[str] = []
    for pattern in _LABEL_RES:
        for label in pattern.findall(src):
            for raw in _HTML_TAG.sub(" ", label).split():
                token = raw.strip(_EDGE_PUNCT).rstrip(".")
                if token.startswith("./"):
                    token = token[2:]
                if not any(ch.isalnum() for ch in token):
                    continue  # a lone "/" or "-" is not a path
                if "/" in token or token.lower().endswith(PATH_EXTENSIONS):
                    tokens.append(token)
    return tokens


def _in_tree(path: str, tree: set[str], dirs: set[str]) -> bool:
    return path.rstrip("/") in tree or path.rstrip("/") in dirs


def check_mermaid(src: str, tree: list[str]) -> MermaidCheck:
    if not src.strip():
        return MermaidCheck(False, "diagram is empty")
    if len(src) > MERMAID_MAX_CHARS:
        return MermaidCheck(False, f"diagram is longer than {MERMAID_MAX_CHARS} characters")
    lowered = src.lower()
    if _FORBIDDEN_WORDS.search(src) or any(bad in lowered for bad in _FORBIDDEN_SUBSTRINGS):
        return MermaidCheck(False, "diagram contains a forbidden construct")
    files = {p.strip("/") for p in tree}
    dirs = {"/".join(p.split("/")[:i]) for p in files for i in range(1, p.count("/") + 1)}
    for token in _path_tokens(src):
        if not _in_tree(token, files, dirs):
            return MermaidCheck(False, f"path not in the repository: {token}")
    return MermaidCheck(True)


def _subprocess_env() -> dict[str, str]:
    """No secrets: only what node and the browser need."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", tempfile.gettempdir()),
        "TMPDIR": tempfile.gettempdir(),
    }
    env.update({k: v for k, v in os.environ.items() if k.startswith("PUPPETEER_")})
    return env


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    """Kill mmdc and the browser it started (own session = own process group)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


async def render_mermaid(src: str, out: Path, settings: Settings | None = None) -> None:
    """Render `src` to the PNG `out`. Raises RenderFailed; never leaves a partial `out`."""
    if settings is None:
        settings = get_settings()
    work = Path(tempfile.mkdtemp(prefix="lb-mmdc-"))  # mode 0700
    try:
        in_file = work / "diagram.mmd"
        in_file.write_text(src, encoding="utf-8")
        args = [
            settings.mmdc_executable,
            "-i", str(in_file),
            "-o", str(out),
            "-c", str(MMDC_CONFIG),
            "-b", "white",
            "--size", str(RENDER_WIDTH_PX),  # mmdc 12 replaced -w with --size
            "-q",
        ]  # fmt: skip
        puppeteer = settings.mmdc_puppeteer_file
        if puppeteer is not None:
            args += ["-p", str(puppeteer)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=_subprocess_env(),
                cwd=work,
                start_new_session=True,
            )
        except OSError as exc:
            raise RenderFailed("renderer is not available") from exc
        try:
            await asyncio.wait_for(proc.wait(), RENDER_TIMEOUT_S)
        except TimeoutError as exc:
            _kill_group(proc)
            await proc.wait()
            raise RenderFailed("rendering timed out") from exc
        except BaseException:  # cancelled: do not leave a browser running
            _kill_group(proc)
            raise
        if proc.returncode != 0:
            raise RenderFailed("renderer failed")
        with out.open("rb") as f:
            if f.read(len(PNG_MAGIC)) != PNG_MAGIC:
                raise RenderFailed("renderer produced no image")
    except FileNotFoundError as exc:
        raise RenderFailed("renderer produced no image") from exc
    except RenderFailed:
        await asyncio.to_thread(out.unlink, missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def source_hash(src: str) -> str:
    return hashlib.sha256(src.encode()).hexdigest()


async def render_diagram(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Check and render one diagram Media row. Idempotent: a `ready` row whose stored source hash
    matches the current source is left alone. Failures are recorded on the row (`meta.reason`)."""
    media_id = uuid.UUID(str(payload["media_id"]))
    root = ctx.settings.media_dir
    async with ctx.sessionmaker() as db:
        row = (
            await db.execute(
                select(Media, Event.context_json)
                .join(Draft, Draft.id == Media.draft_id)
                .join(Event, Event.id == Draft.event_id)
                .where(Media.id == media_id)
            )
        ).one_or_none()
        if row is None:
            return {"skipped": "media not found"}
        media, context_json = row
        if media.type != MediaType.diagram:
            return {"skipped": "not a diagram"}
        src = media.mermaid_source or ""
        digest = source_hash(src)
        if (
            media.status == MediaStatus.ready
            and media.meta.get("source_sha256") == digest
            and media.file_path
            and media_path(media.file_path, root).is_file()
        ):
            return {"skipped": "already_rendered"}
    tree = list((context_json or {}).get("file_tree") or [])

    checked = check_mermaid(src, tree)
    new_name: str | None = None
    reason = checked.reason
    if checked.ok:
        new_name = new_media_name("png")
        root.mkdir(parents=True, exist_ok=True)
        try:
            await render_mermaid(src, media_path(new_name, root), ctx.settings)
        except RenderFailed as exc:
            new_name, reason = None, str(exc)

    old_name: str | None = None
    async with ctx.sessionmaker() as db:
        current = await db.scalar(select(Media).where(Media.id == media_id).with_for_update())
        if current is None or source_hash(current.mermaid_source or "") != digest:
            # The owner edited the source while we rendered: drop our output and run again. Defer
            # re-queues this very job (its dedupe key made the edit's own enqueue a no-op).
            if new_name:
                media_path(new_name, root).unlink(missing_ok=True)
            if current is None:
                return {"skipped": "media deleted"}
            raise Defer(datetime.now(UTC))
        meta = {k: v for k, v in current.meta.items() if k != "reason"}
        meta["source_sha256"] = digest
        if new_name:
            old_name = current.file_path
            current.file_path = new_name
            current.mime = "image/png"
            current.status = MediaStatus.ready
        else:
            meta["reason"] = reason
            current.status = MediaStatus.failed
        current.meta = meta
        await db.commit()
    if old_name and old_name != new_name:  # only after the new file is committed
        try:
            media_path(old_name, root).unlink(missing_ok=True)
        except ValueError:
            pass
    return {"status": "ready"} if new_name else {"status": "failed", "reason": reason}


HANDLERS["render_diagram"] = render_diagram
