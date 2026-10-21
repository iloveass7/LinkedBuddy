"""Screenshot uploads: decode with Pillow, then re-encode so no original bytes or metadata survive
(SECURITY.md 5.1, 5.2). The format comes from decoding, never from the file name or Content-Type."""

import asyncio
import io
import warnings
from typing import Literal

from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SIDE_PX = 8000
Image.MAX_IMAGE_PIXELS = 64_000_000  # Pillow warns above this and raises above 2x

_ALLOWED = {"PNG", "JPEG", "WEBP"}

MSG_TOO_LARGE = "image is larger than 10 MB"
MSG_FORMAT = "only PNG, JPEG and WebP images are accepted"
MSG_DIMENSIONS = "image dimensions are too large"
MSG_INVALID = "file is not a valid image"


def _reencode(data: bytes) -> tuple[bytes, Literal["png", "jpg"]]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(MSG_TOO_LARGE)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                fmt = img.format
                if fmt not in _ALLOWED:
                    raise ValueError(MSG_FORMAT)
                if max(img.size) > MAX_SIDE_PX:
                    raise ValueError(MSG_DIMENSIONS)
                img.load()  # decode fully: a truncated or corrupt file fails here
                # Bake the EXIF rotation into the pixels before the EXIF is dropped.
                pixels = ImageOps.exif_transpose(img)
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError(MSG_DIMENSIONS) from exc
    except Exception as exc:  # Pillow raises OSError, SyntaxError, struct.error, ... on bad data
        raise ValueError(MSG_INVALID) from exc

    jpeg = fmt == "JPEG"
    if jpeg:
        pixels = pixels.convert("RGB") if pixels.mode not in ("RGB", "L") else pixels
    elif pixels.mode not in ("RGB", "RGBA", "L", "LA"):
        pixels = pixels.convert("RGBA")
    pixels.info = {}  # nothing (EXIF, ICC, text chunks, XMP) is carried into the output
    out = io.BytesIO()
    if jpeg:
        pixels.save(out, format="JPEG", quality=90)
    else:
        pixels.save(out, format="PNG")
    return out.getvalue(), "jpg" if jpeg else "png"


async def reencode_upload(data: bytes) -> tuple[bytes, Literal["png", "jpg"]]:
    """Validated, metadata-free PNG or JPEG bytes (WebP becomes PNG). ValueError, with a fixed
    message, when the upload is not an acceptable image."""
    return await asyncio.to_thread(_reencode, data)
