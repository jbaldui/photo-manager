"""Unified, read-only image loading for every supported format."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    if hasattr(pillow_heif, "register_avif_opener"):
        pillow_heif.register_avif_opener()
except Exception:  # pragma: no cover
    pass

Image.MAX_IMAGE_PIXELS = 400_000_000
ImageFile.LOAD_TRUNCATED_IMAGES = True

RAW_EXTS = {"cr2", "cr3", "nef", "nrw", "arw", "srf", "sr2", "dng", "orf", "rw2", "raf", "srw", "pef", "rwl", "3fr", "iiq", "x3f"}
IMAGE_EXTS = {
    "jpg", "jpeg", "jpe", "jfif", "pjpeg", "png", "apng", "gif", "webp", "bmp", "dib",
    "tif", "tiff", "heic", "heif", "avif", "ico",
} | RAW_EXTS

# Magic bytes for files with odd or missing extensions (e.g. "meme.jpg_large", "download").
_MAGIC = [
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
]


def sniff(path: str) -> str | None:
    """Return a format name if the file starts with a known image signature."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return None
    for sig, name in _MAGIC:
        if head.startswith(sig):
            return name
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"avif", b"hevc"):
        return "heic"
    return None


def open_image(path: str | Path, data: bytes | None = None) -> Image.Image:
    """Open an image (no pixel decode yet for normal formats). RAW files return their embedded preview."""
    path = str(path)
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in RAW_EXTS:
        return _open_raw(path, data)
    src = io.BytesIO(data) if data is not None else path
    return Image.open(src)


def _open_raw(path: str, data: bytes | None) -> Image.Image:
    import numpy as np
    import rawpy

    src = io.BytesIO(data) if data is not None else path
    with rawpy.imread(src) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                img = Image.open(io.BytesIO(thumb.data))
                img.load()
                return img
            if thumb.format == rawpy.ThumbFormat.BITMAP:
                return Image.fromarray(np.asarray(thumb.data))
        except Exception:
            pass
        rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb)


def to_working_rgb(img: Image.Image, max_side: int) -> Image.Image:
    """Fast decode at reduced size, apply EXIF orientation, flatten alpha onto white."""
    if img.format == "JPEG":
        img.draft("RGB", (max_side, max_side))  # DCT-domain downscale: big speedup
    try:
        img.seek(0)
    except Exception:
        pass
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    return img
