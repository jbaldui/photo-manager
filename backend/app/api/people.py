"""People: face clusters, naming, merging, and corrections."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, db, decode, faces
from .common import abs_path

router = APIRouter(prefix="/api")
CROP_DIR = config.THUMB_DIR / "faces"
CROP_SIZE = 160


def _person_row(r) -> dict:
    return {
        "id": r["id"], "name": r["name"], "faces": r["n_faces"], "photos": r["n_photos"],
        "cover_face_id": r["cover_face_id"], "hidden": bool(r["hidden"]),
    }


@router.get("/people")
def list_people(include_hidden: bool = False, min_faces: int = 1):
    rows = db.get().execute(
        """SELECT p.id, p.name, p.cover_face_id, p.hidden, COUNT(f.id) AS n_faces, COUNT(DISTINCT f.sha256) AS n_photos
           FROM people p JOIN faces f ON f.person_id=p.id
           WHERE (? OR p.hidden=0) GROUP BY p.id HAVING n_faces >= ?
           ORDER BY (p.name IS NULL), n_photos DESC""",
        (1 if include_hidden else 0, min_faces),
    ).fetchall()
    conn = db.get()
    return {
        "people": [_person_row(r) for r in rows],
        "unassigned": conn.execute("SELECT COUNT(*) FROM faces WHERE person_id IS NULL").fetchone()[0],
        "faces": conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0],
        "scanned": conn.execute("SELECT COUNT(*) FROM faces_done").fetchone()[0],
        "threshold": faces.get_threshold(),
    }


class PersonPatch(BaseModel):
    name: str | None = None
    hidden: bool | None = None
    cover_face_id: int | None = None


@router.patch("/people/{pid}")
def patch_person(pid: int, body: PersonPatch):
    sets, args = [], []
    if body.name is not None:
        name = body.name.strip() or None
        dup = name and db.get().execute("SELECT id FROM people WHERE name=? COLLATE NOCASE AND id<>?", (name, pid)).fetchone()
        if dup:
            raise HTTPException(409, f"'{name}' already exists (id {dup['id']}); merge the two people instead")
        sets.append("name=?")
        args.append(name)
    if body.hidden is not None:
        sets.append("hidden=?")
        args.append(int(body.hidden))
    if body.cover_face_id is not None:
        sets.append("cover_face_id=?")
        args.append(body.cover_face_id)
    if not sets:
        raise HTTPException(400, "nothing to change")
    with db.tx() as c:
        if not c.execute(f"UPDATE people SET {', '.join(sets)} WHERE id=?", args + [pid]).rowcount:
            raise HTTPException(404)
    return {"ok": True}


class Merge(BaseModel):
    into: int


@router.post("/people/{pid}/merge")
def merge_person(pid: int, body: Merge):
    """Move every face of `pid` into `body.into` (pinned, so re-clustering keeps it) and delete `pid`."""
    if pid == body.into:
        raise HTTPException(400, "cannot merge a person into themselves")
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM people WHERE id=?", (body.into,)).fetchone() or not c.execute("SELECT 1 FROM people WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404)
        n = c.execute("UPDATE faces SET person_id=?, manual=1 WHERE person_id=?", (body.into, pid)).rowcount
        c.execute("DELETE FROM people WHERE id=?", (pid,))
    return {"moved": n}


@router.delete("/people/{pid}")
def delete_person(pid: int):
    """Remove a person; their faces become unassigned and are free to be re-grouped on the next clustering."""
    with db.tx() as c:
        c.execute("UPDATE faces SET person_id=NULL, manual=0 WHERE person_id=?", (pid,))
        c.execute("DELETE FROM people WHERE id=?", (pid,))
    return {"ok": True}


@router.get("/people/{pid}/faces")
def person_faces(pid: int, limit: int = 200, offset: int = 0):
    rows = db.get().execute(
        """SELECT fa.id AS face_id, fa.sha256, fa.score, (SELECT f.id FROM files f WHERE f.sha256=fa.sha256 AND f.status='ok' ORDER BY f.id LIMIT 1) AS file_id
           FROM faces fa WHERE fa.person_id=? ORDER BY fa.score DESC LIMIT ? OFFSET ?""",
        (pid, limit, offset),
    ).fetchall()
    return {"faces": [dict(r) for r in rows if r["file_id"]]}


class FaceAssign(BaseModel):
    person_id: int | None = None      # None = "not a face / unassign"
    new_person_name: str | None = None


@router.post("/faces/{face_id}/assign")
def assign_face(face_id: int, body: FaceAssign):
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM faces WHERE id=?", (face_id,)).fetchone():
            raise HTTPException(404)
        pid = body.person_id
        if body.new_person_name:
            pid = c.execute("INSERT INTO people(name, created) VALUES(?, ?)", (body.new_person_name.strip(), time.time())).lastrowid
        c.execute("UPDATE faces SET person_id=?, manual=? WHERE id=?", (pid, 1 if pid else 0, face_id))
        if pid:
            c.execute("UPDATE people SET cover_face_id=? WHERE id=? AND cover_face_id IS NULL", (face_id, pid))
    return {"person_id": pid}


@router.post("/faces/cluster")
def recluster(threshold: float | None = None):
    """Re-run grouping (cheap; no image decoding). Optionally change the match threshold (0.3-0.8)."""
    if threshold is not None:
        faces.set_threshold(max(0.3, min(0.8, threshold)))
    return faces.cluster()


@router.get("/faces/{face_id}/crop")
def face_crop(face_id: int):
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    out = CROP_DIR / f"{face_id}.jpg"
    if out.exists():
        return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    r = db.get().execute(
        "SELECT x0, y0, x1, y1, (SELECT f.id FROM files f WHERE f.sha256=faces.sha256 AND f.status='ok' ORDER BY f.id LIMIT 1) AS file_id FROM faces WHERE id=?",
        (face_id,),
    ).fetchone()
    if not r or not r["file_id"]:
        raise HTTPException(404)
    p = abs_path(r["file_id"])
    if not p or not p.is_file():
        raise HTTPException(404)
    try:
        img = decode.to_working_rgb(decode.open_image(p), faces.READ_SIZE)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(415, f"Cannot render: {e}")
    w, h = img.size
    cx, cy = (r["x0"] + r["x1"]) / 2 * w, (r["y0"] + r["y1"]) / 2 * h
    half = max(r["x1"] - r["x0"], r["y1"] - r["y0"]) * max(w, h) * 0.5 * 1.5      # 50% margin around the box
    crop = img.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))).resize((CROP_SIZE, CROP_SIZE))
    crop.save(out, "JPEG", quality=85)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
