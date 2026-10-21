"""Where media files live. Names are server-generated; every path is checked against MEDIA_ROOT
(SECURITY.md 5.3)."""

import uuid
from pathlib import Path
from typing import Literal

from linkedbuddy.config import get_settings

Ext = Literal["png", "jpg", "mp4"]


def media_path(name: str, root: Path | None = None) -> Path:
    """`name` under MEDIA_ROOT (default: the configured one). ValueError if it would escape."""
    base = (root if root is not None else get_settings().media_dir).resolve()
    path = (base / name).resolve()  # also follows symlinks, so a link out of the root is caught
    if path == base or not path.is_relative_to(base):
        raise ValueError("path escapes media root")
    return path


def new_media_name(ext: Ext) -> str:
    return f"{uuid.uuid4()}.{ext}"
