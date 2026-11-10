from __future__ import annotations

import json
from pathlib import Path

from .. import db

FILE_LIST_COLS = (
    "f.id, f.root_id, f.rel_path, f.name, f.ext, f.size, f.mtime, f.status, f.sha256, f.format, f.width, f.height,"
    " f.date_taken, f.date_source, f.camera_make, f.camera_model, f.source_guess, f.has_exif, f.error"
)


def file_row(r) -> dict:
    d = dict(r)
    d["thumb_url"] = f"/api/photos/{d['id']}/thumb" if d.get("sha256") else None
    return d


def decisions_for(file_ids: list[int]) -> dict[int, str]:
    if not file_ids:
        return {}
    conn = db.get()
    out = {}
    for s in range(0, len(file_ids), 900):
        chunk = file_ids[s : s + 900]
        q = f"""SELECT f.id, d.decision FROM files f JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path
                WHERE f.id IN ({','.join('?' * len(chunk))})"""
        out.update({r["id"]: r["decision"] for r in conn.execute(q, chunk)})
    return out


def abs_path(file_id: int) -> Path | None:
    r = db.get().execute(
        "SELECT r.path, f.rel_path FROM files f JOIN roots r ON r.id=f.root_id WHERE f.id=?", (file_id,)
    ).fetchone()
    return Path(r["path"]) / r["rel_path"] if r else None


def full_file(file_id: int) -> dict | None:
    r = db.get().execute(
        "SELECT f.*, r.path AS root_path FROM files f JOIN roots r ON r.id=f.root_id WHERE f.id=?", (file_id,)
    ).fetchone()
    if not r:
        return None
    d = dict(r)
    d["exif"] = json.loads(d.pop("exif_json") or "null")
    d["abs_path"] = str(Path(d["root_path"]) / d["rel_path"])
    d["thumb_url"] = f"/api/photos/{d['id']}/thumb" if d.get("sha256") else None
    d["decision"] = decisions_for([file_id]).get(file_id)
    return d
