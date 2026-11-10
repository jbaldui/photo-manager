"""EXIF / container metadata extraction, date normalisation and source guessing."""
from __future__ import annotations

import json
import re
from datetime import datetime

from PIL import ExifTags, Image

_TAGS = ExifTags.TAGS
_GPSTAGS = ExifTags.GPSTAGS
IFD_EXIF = 0x8769
IFD_GPS = 0x8825


def _clean(v):
    if isinstance(v, bytes):
        if len(v) > 64:
            return f"<{len(v)} bytes>"
        try:
            return v.decode("utf-8", "replace").strip("\x00 ")
        except Exception:
            return repr(v)
    if isinstance(v, tuple):
        return [_clean(x) for x in v]
    if isinstance(v, (bool, int, str)):
        return v
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        try:
            return round(float(v), 6)
        except ZeroDivisionError:
            return None
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)


def _parse_exif_dt(s) -> str | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip("\x00 ")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(s[:19], fmt)
            if 1970 <= dt.year <= 2100:
                return dt.isoformat()
        except ValueError:
            continue
    return None


_FNAME_DATE = [
    # IMG_20190812_153012, PXL_20210101_101010123, VID-20190812-WA0001, Screenshot_20200101-101010
    re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})[_\-T ]?(\d{2})(\d{2})(\d{2})"),
    # WhatsApp Image 2019-08-12 at 15.30.12, Screen Shot 2019-08-12 at 3.30.12 PM
    re.compile(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?:\D+(\d{1,2})[.:](\d{2})[.:](\d{2}))?"),
    # IMG-20190812-WA0001 (date only)
    re.compile(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)"),
]


def date_from_filename(name: str) -> str | None:
    for rx in _FNAME_DATE:
        m = rx.search(name)
        if not m:
            continue
        g = [int(x) if x else 0 for x in m.groups()]
        g += [0] * (6 - len(g))
        try:
            dt = datetime(g[0], g[1], g[2], g[3], g[4], g[5])
        except ValueError:
            continue
        if 1990 <= dt.year <= 2100:
            return dt.isoformat()
    return None


_SOURCES = [
    (re.compile(r"whatsapp|^IMG-\d{8}-WA\d+", re.I), "WhatsApp"),
    (re.compile(r"telegram|^photo_\d{4}-\d{2}-\d{2}_", re.I), "Telegram"),
    (re.compile(r"^FB_IMG|facebook", re.I), "Facebook"),
    (re.compile(r"^received_\d+", re.I), "Messenger"),
    (re.compile(r"instagram|^IMG_\d{4}_\d+_\d+\.", re.I), "Instagram"),
    (re.compile(r"screenshot|screen shot|captura de tela|bildschirmfoto|^Capture", re.I), "Screenshot"),
    (re.compile(r"^signal-\d{4}", re.I), "Signal"),
    (re.compile(r"^snapchat", re.I), "Snapchat"),
    (re.compile(r"^PXL_\d{8}", re.I), "Phone camera"),
    (re.compile(r"^(IMG|VID)_\d{8}_\d{6}", re.I), "Phone camera"),
]


def guess_source(name: str, make: str | None, software: str | None, has_exif: bool, fmt: str | None) -> str:
    for rx, label in _SOURCES:
        if rx.search(name):
            return label
    sw = (software or "").lower()
    if "instagram" in sw:
        return "Instagram"
    if any(s in sw for s in ("photoshop", "gimp", "lightroom", "snapseed", "picasa", "paint")):
        return "Edited"
    if make:
        return "Camera"
    if fmt == "PNG":
        return "Graphic / download"
    if not has_exif:
        return "Unknown (no metadata)"
    return "Unknown"


def _gps_to_deg(vals, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in vals)
        deg = d + m / 60 + s / 3600
        if ref in ("S", "W"):
            deg = -deg
        return round(deg, 6)
    except Exception:
        return None


def extract(img: Image.Image, name: str) -> dict:
    """Pull metadata from a PIL image object (header only; no pixel decode)."""
    out: dict = {
        "format": img.format,
        "mode": img.mode,
        "width": img.width,
        "height": img.height,
        "has_alpha": int(img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info),
        "frames": int(getattr(img, "n_frames", 1) or 1),
    }
    flat: dict = {}
    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if exif:
        for k, v in exif.items():
            flat[_TAGS.get(k, f"0x{k:04x}")] = _clean(v)
        try:
            for k, v in exif.get_ifd(IFD_EXIF).items():
                flat[_TAGS.get(k, f"0x{k:04x}")] = _clean(v)
        except Exception:
            pass
        gps = {}
        try:
            for k, v in exif.get_ifd(IFD_GPS).items():
                gps[_GPSTAGS.get(k, f"0x{k:04x}")] = _clean(v)
        except Exception:
            pass
        if gps:
            flat["GPS"] = gps
            out["gps_lat"] = _gps_to_deg(gps.get("GPSLatitude") or (), gps.get("GPSLatitudeRef"))
            out["gps_lon"] = _gps_to_deg(gps.get("GPSLongitude") or (), gps.get("GPSLongitudeRef"))
    # PNG text chunks / other container info that is useful to show
    for key in ("Creation Time", "Software", "Author", "Description", "Comment", "dpi"):
        if key in img.info and key not in flat:
            flat[key] = _clean(img.info[key])

    flat.pop("MakerNote", None)
    flat.pop("ExifOffset", None)
    flat.pop("GPSInfo", None)

    has_exif = bool(exif) and len(exif) > 0
    out["has_exif"] = int(has_exif)
    out["camera_make"] = (flat.get("Make") or None) and str(flat["Make"]).strip() or None
    out["camera_model"] = (flat.get("Model") or None) and str(flat["Model"]).strip() or None
    out["lens"] = flat.get("LensModel") or None
    out["software"] = flat.get("Software") or None
    out["orientation"] = flat.get("Orientation") if isinstance(flat.get("Orientation"), int) else None

    date, source = None, None
    for tag, label in (("DateTimeOriginal", "exif_original"), ("DateTimeDigitized", "exif_digitized"), ("DateTime", "exif_modified")):
        date = _parse_exif_dt(flat.get(tag))
        if date:
            source = label
            break
    if not date:
        date = date_from_filename(name)
        source = "filename" if date else None
    out["date_taken"] = date
    out["date_source"] = source
    out["source_guess"] = guess_source(name, out["camera_make"], out["software"], has_exif, img.format)
    out["exif_json"] = json.dumps(flat, default=str, ensure_ascii=False) if flat else None
    return out
