"""Subprocess hygiene shared by the renderers (SECURITY.md 5.5, 5.6): a minimal environment and a
reaper that leaves nothing of a render running, on every exit path (Linux /proc)."""

import asyncio
import os
import signal
import tempfile
from pathlib import Path

TERM_GRACE_S = 2.5  # SIGTERM first: Puppeteer closes its browser when mmdc is terminated


def minimal_env(work: Path) -> dict[str, str]:
    """No secrets: PATH, HOME and TMPDIR (the render's own directory) only."""
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", tempfile.gettempdir()),
        "TMPDIR": str(work),
    }


def _children(pid: int) -> list[int]:
    found: list[int] = []
    try:
        threads = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return found
    for tid in threads:
        try:
            with open(f"/proc/{pid}/task/{tid}/children", encoding="ascii") as f:
                found += [int(p) for p in f.read().split()]
        except (OSError, ValueError):
            continue
    return found


def _descendants(pid: int) -> list[int]:
    """Every process below `pid`, found through /proc (Linux)."""
    seen: list[int] = []
    stack = [pid]
    while stack:
        for child in _children(stack.pop()):
            if child not in seen:
                seen.append(child)
                stack.append(child)
    return seen


def _pids_mentioning(needle: bytes) -> list[int]:
    """Processes whose command line contains `needle` (this render's private directory)."""
    me = os.getpid()
    found: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                if needle in f.read():
                    found.append(int(entry))
        except OSError:
            continue
    return found


def _sigkill(pids: list[int]) -> None:
    own_group = os.getpgrp()
    for pid in pids:
        try:
            group = os.getpgid(pid)
            if group != own_group:
                os.killpg(group, signal.SIGKILL)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        pass


async def reap(proc: asyncio.subprocess.Process, work: Path) -> None:
    """Leave nothing of this render running, on every exit path. Puppeteer starts the browser
    detached (its own process group), so killing mmdc's group is not enough: the descendants are
    collected before mmdc is stopped (afterwards they are re-parented), and anything still
    carrying the render's private directory on its command line is killed too."""
    descendants: list[int] = []
    if proc.returncode is None:
        descendants = await asyncio.to_thread(_descendants, proc.pid)
        _signal_group(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), TERM_GRACE_S)
        except TimeoutError:
            pass
    leftovers = await asyncio.to_thread(_pids_mentioning, str(work).encode())
    await asyncio.to_thread(_sigkill, [*descendants, *leftovers])
    _signal_group(proc.pid, signal.SIGKILL)
    if proc.returncode is None:
        await proc.wait()
