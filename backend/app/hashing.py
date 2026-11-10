"""Per-file analysis worker: SHA-256, metadata, perceptual hashes and thumbnail.

Runs inside a process pool. Each file is read from disk exactly once and never written.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import imagehash
from PIL import Image

from . import config, decode, metadata


def to_signed64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def to_unsigned64(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


def _h(hash_obj) -> int:
    return to_signed64(int(str(hash_obj), 16))


def thumb_path(sha: str) -> Path:
    return config.THUMB_DIR / sha[:2] / f"{sha}.webp"


def trim_bars(img: Image.Image, bg_frac: float = 0.5, max_sat: float = 0.08, min_trim: float = 0.03) -> Image.Image | None:
    """Strip caption bars / borders: bands at the edges that are colourless and mostly white or black.

    A row (column) is part of a bar when >= `bg_frac` of its pixels are near-white/near-black and
    its mean saturation is below `max_sat` - black caption text on a white bar passes, photo rows
    (colourful, mid-tones) do not. Returns the inner content region, or None if nothing to trim.
    """
    import numpy as np

    small = img.convert("RGB")
    small.thumbnail((256, 256))
    rgb = np.asarray(small, dtype=np.int16)
    h, w = rgb.shape[:2]
    if h < 16 or w < 16:
        return None
    lum = rgb.mean(axis=2)
    sat = (rgb.max(axis=2) - rgb.min(axis=2)) / 255.0
    extreme = (lum > 215) | (lum < 40)

    def is_bar(ext, s):
        return (ext >= bg_frac) & (s <= max_sat)

    rows = is_bar(extreme.mean(axis=1), sat.mean(axis=1))
    cols = is_bar(extreme.mean(axis=0), sat.mean(axis=0))

    def band(flags):
        n = 0
        for f in flags:
            if not f:
                break
            n += 1
        return n

    top, bottom = band(rows), band(rows[::-1])
    left, right = band(cols), band(cols[::-1])
    if top + bottom >= h * 0.8 or left + right >= w * 0.8:
        return None
    if (top + bottom) / h < min_trim and (left + right) / w < min_trim:
        return None
    sx, sy = img.width / w, img.height / h
    return img.crop((int(left * sx), int(top * sy), int((w - right) * sx), int((h - bottom) * sy)))


def perceptual(img: Image.Image) -> dict:
    """The four perceptual hashes plus pHash variants: rot90, rot180, rot270, mirror, bars-trimmed."""
    trimmed = trim_bars(img)
    variants = [
        img.transpose(Image.Transpose.ROTATE_90),
        img.transpose(Image.Transpose.ROTATE_180),
        img.transpose(Image.Transpose.ROTATE_270),
        img.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        trimmed if trimmed is not None else img,
    ]
    return {
        "phash": _h(imagehash.phash(img)),
        "dhash": _h(imagehash.dhash(img)),
        "whash": _h(imagehash.whash(img)),
        "colorhash": str(imagehash.colorhash(img, binbits=3)),
        "phash_variants": ",".join(str(_h(imagehash.phash(v))) for v in variants),
    }


_KNOWN: set[str] = set()


def init_worker(db_path: str) -> None:
    """Process-pool initializer: load the set of already-analysed content hashes once."""
    import sqlite3

    global _KNOWN
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        _KNOWN = {r[0] for r in conn.execute("SELECT sha256 FROM images WHERE thumb IS NOT NULL")}
        conn.close()
    except Exception:
        _KNOWN = set()


def analyze_file(path: str, known_shas: set[str] | None = None) -> dict:
    """Analyze one file. Returns a dict of DB fields, or {'error': ...}."""
    try:
        with open(path, "rb") as f:          # read-only
            data = f.read()
        sha = hashlib.sha256(data).hexdigest()
        name = os.path.basename(path)
        img = decode.open_image(path, data)
        meta = metadata.extract(img, name)
        out = {"sha256": sha, **meta}
        if meta["orientation"] in (5, 6, 7, 8):   # stored rotated: report displayed size
            out["width"], out["height"] = out["height"], out["width"]
        tp = thumb_path(sha)
        known = _KNOWN if known_shas is None else known_shas
        if sha in known and tp.exists():
            out["image"] = None              # pixel data already analysed for this content
            return out
        work = decode.to_working_rgb(img, config.WORK_SIZE)
        hsrc = work.copy()
        hsrc.thumbnail((config.HASH_SIZE, config.HASH_SIZE), Image.Resampling.BILINEAR)
        imgrow = {"sha256": sha, "width": out["width"], "height": out["height"], **perceptual(hsrc)}
        tp.parent.mkdir(parents=True, exist_ok=True)
        if not tp.exists():
            t = work.copy()
            t.thumbnail((config.THUMB_SIZE, config.THUMB_SIZE), Image.Resampling.LANCZOS)
            tmp = tp.with_suffix(f".{os.getpid()}.tmp")
            t.save(tmp, "WEBP", quality=80, method=4)
            os.replace(tmp, tp)
        imgrow["thumb"] = str(tp.relative_to(config.THUMB_DIR))
        out["image"] = imgrow
        return out
    except Exception as e:  # noqa: BLE001 - any decode failure is recorded, never fatal
        return {"error": f"{type(e).__name__}: {e}"[:500]}


def analyze_batch(paths: list[tuple[int, str]]) -> list[tuple[int, dict]]:
    return [(fid, analyze_file(p)) for fid, p in paths]
