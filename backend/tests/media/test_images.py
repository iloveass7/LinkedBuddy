import io
from pathlib import Path

import pytest
from PIL import Image

from linkedbuddy.media import images
from linkedbuddy.media.images import reencode_upload
from linkedbuddy.media.storage import media_path, new_media_name


def _image_bytes(fmt: str, size: tuple[int, int] = (20, 10), **save: object) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, format=fmt, **save)
    return out.getvalue()


def _exif(orientation: int | None = None) -> Image.Exif:
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"  # Make
    exif[0x013B] = "Jane Doe"  # Artist
    if orientation:
        exif[0x0112] = orientation
    return exif


async def test_reencode_strips_exif() -> None:
    raw = _image_bytes("JPEG", exif=_exif())
    assert len(Image.open(io.BytesIO(raw)).getexif()) > 0

    out, ext = await reencode_upload(raw)

    assert ext == "jpg"
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert len(img.getexif()) == 0
    assert b"SecretCamera" not in out and b"Jane Doe" not in out


async def test_reencode_applies_exif_rotation_before_dropping_it() -> None:
    out, _ = await reencode_upload(_image_bytes("JPEG", (20, 10), exif=_exif(orientation=6)))
    assert Image.open(io.BytesIO(out)).size == (10, 20)


async def test_reencode_strips_png_text_chunks() -> None:
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("Comment", "private-note")
    raw = _image_bytes("PNG", pnginfo=info)
    assert b"private-note" in raw

    out, ext = await reencode_upload(raw)

    assert ext == "png" and b"private-note" not in out
    assert Image.open(io.BytesIO(out)).format == "PNG"


async def test_webp_becomes_png() -> None:
    out, ext = await reencode_upload(_image_bytes("WEBP"))
    assert ext == "png"
    assert Image.open(io.BytesIO(out)).format == "PNG"


async def test_palette_png_is_converted() -> None:
    out = io.BytesIO()
    Image.new("P", (5, 5)).save(out, format="PNG")
    data, ext = await reencode_upload(out.getvalue())
    assert ext == "png" and Image.open(io.BytesIO(data)).mode in ("RGB", "RGBA")


async def test_reject_non_image() -> None:
    with pytest.raises(ValueError, match="not a valid image"):
        await reencode_upload(b"%PDF-1.4")


async def test_reject_other_image_format() -> None:
    with pytest.raises(ValueError, match="only PNG, JPEG and WebP"):
        await reencode_upload(_image_bytes("GIF"))


async def test_reject_too_large() -> None:
    with pytest.raises(ValueError, match="10 MB"):
        await reencode_upload(b"\x00" * (11 * 1024 * 1024))


async def test_reject_wide_image() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        await reencode_upload(_image_bytes("PNG", (8001, 1)))


async def test_reject_decompression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ValueError, match="dimensions"):
        await reencode_upload(_image_bytes("PNG", (50, 50)))  # 2500 px > 2x the 100 px limit
    with pytest.raises(ValueError, match="dimensions"):
        await reencode_upload(_image_bytes("PNG", (15, 10)))  # 150 px: warning, treated as error


async def test_reject_truncated_image() -> None:
    with pytest.raises(ValueError, match="not a valid image"):
        await reencode_upload(_image_bytes("PNG", (200, 200))[:80])


def test_pixel_limit_is_set() -> None:
    assert images.Image.MAX_IMAGE_PIXELS == 64_000_000


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "a/../../x.png", "", "."])
def test_media_path_traversal(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        media_path(bad, tmp_path)


def test_media_path_rejects_symlink_out(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        media_path("link/x.png", root)


def test_media_path_inside(tmp_path: Path) -> None:
    assert media_path("a.png", tmp_path) == tmp_path.resolve() / "a.png"


def test_new_media_name_is_server_generated() -> None:
    a, b = new_media_name("png"), new_media_name("jpg")
    assert a.endswith(".png") and b.endswith(".jpg") and a != b
    assert "/" not in a and ".." not in a
