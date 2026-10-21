"""The `brag` adapter: the only module that knows how the external renderer is invoked.

Contract (owner-confirmed interface may change only this file): `brag_command + [script.json,
out.mp4]`, where script.json is `{"script": {...}, "assets": {"<media id>": "<absolute path>"}}`.
It runs as an argument list (no shell) in its own private temp dir, with an environment of PATH,
HOME and TMPDIR only (no secrets), under a timeout, and is always reaped (SECURITY.md 5.6).
"""

import asyncio
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from linkedbuddy.media.subproc import minimal_env, reap
from linkedbuddy.video.renderer import RenderFailed, VideoScript

MP4_BRAND_OFFSET = 4  # an MP4 starts with a box size (4 bytes), then the type: b"ftyp"


class BragRenderer:
    def __init__(self, command: list[str], timeout_s: int) -> None:
        if not command:
            raise ValueError("brag command is empty")
        self.command = list(command)
        self.timeout_s = timeout_s

    async def render(self, script: VideoScript, assets: dict[uuid.UUID, Path], out: Path) -> None:
        work = Path(tempfile.mkdtemp(prefix="lb-brag-"))  # mode 0700
        proc: asyncio.subprocess.Process | None = None
        try:
            json_path = work / "script.json"
            payload = {
                "script": script.model_dump(mode="json"),
                "assets": {str(k): str(v.resolve()) for k, v in assets.items()},
            }
            json_path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self.command,
                    str(json_path),
                    str(out),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=minimal_env(work),
                    cwd=work,
                    start_new_session=True,
                )
            except OSError as exc:
                raise RenderFailed("renderer is not available") from exc
            try:
                await asyncio.wait_for(proc.wait(), self.timeout_s)
            except TimeoutError as exc:
                raise RenderFailed("rendering timed out") from exc
            if proc.returncode != 0:
                raise RenderFailed("renderer failed")
            _check_mp4(out)
        except RenderFailed:
            await asyncio.to_thread(out.unlink, missing_ok=True)
            raise
        finally:
            if proc is not None:
                await reap(proc, work)
            shutil.rmtree(work, ignore_errors=True)


def _check_mp4(out: Path) -> None:
    try:
        with out.open("rb") as f:
            head = f.read(MP4_BRAND_OFFSET + 4)
    except OSError as exc:
        raise RenderFailed("renderer produced no video") from exc
    if not head:
        raise RenderFailed("renderer produced no video")
    if head[MP4_BRAND_OFFSET:] != b"ftyp":
        raise RenderFailed("output is not an mp4")
