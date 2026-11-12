"""Folder browsing, roots, pipeline control, settings, stats and export."""
from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, db, embed
from ..jobs import STAGES, runner

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------- folder picker
@router.get("/fs/browse")
def browse(path: str | None = None, show_hidden: bool = False):
    p = Path(path).expanduser() if path else Path.home()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404, f"Folder not found: {path}")
    if not p.is_dir():
        raise HTTPException(400, "Not a folder")
    dirs = []
    try:
        with os.scandir(p) as it:
            for e in it:
                if (show_hidden or not e.name.startswith(".")) and e.is_dir(follow_symlinks=True):
                    dirs.append({"name": e.name, "path": str(Path(e.path))})
    except PermissionError:
        raise HTTPException(403, "Permission denied")
    dirs.sort(key=lambda d: d["name"].lower())
    shortcuts = [{"name": "Home", "path": str(Path.home())}]
    for name in ("Pictures", "Desktop", "Downloads"):
        q = Path.home() / name
        if q.is_dir():
            shortcuts.append({"name": name, "path": str(q)})
    for mnt in ("/media", "/mnt", "/run/media"):
        if os.path.isdir(mnt):
            shortcuts.append({"name": mnt, "path": mnt})
    return {
        "path": str(p),
        "parent": str(p.parent) if p.parent != p else None,
        "dirs": dirs,
        "shortcuts": shortcuts,
    }


# ---------------------------------------------------------------- roots
class RootIn(BaseModel):
    path: str


@router.get("/roots")
def roots():
    conn = db.get()
    rows = conn.execute(
        """SELECT r.*, COUNT(f.id) AS files, SUM(f.status='ok') AS ok, SUM(f.status='pending') AS pending,
                  SUM(f.status='error') AS errors, SUM(f.status='missing') AS missing
           FROM roots r LEFT JOIN files f ON f.root_id=r.id GROUP BY r.id ORDER BY r.id"""
    ).fetchall()
    return [dict(r) | {"exists": os.path.isdir(r["path"])} for r in rows]


@router.post("/roots")
def add_root(body: RootIn):
    p = Path(body.path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(400, "Folder does not exist")
    if p == config.DATA_DIR or config.DATA_DIR in p.parents:
        raise HTTPException(400, "That folder is inside the app's own data directory")
    conn = db.get()
    for r in conn.execute("SELECT path FROM roots"):
        other = Path(r["path"])
        if other == p:
            raise HTTPException(409, "Folder already added")
        if other in p.parents or p in other.parents:
            raise HTTPException(409, f"Overlaps with an existing folder: {other}")
    with db.tx() as c:
        rid = c.execute("INSERT INTO roots(path, added_at) VALUES(?, ?)", (str(p), time.time())).lastrowid
    return {"id": rid, "path": str(p)}


@router.delete("/roots/{root_id}")
def remove_root(root_id: int):
    """Forget a folder and everything derived from it; groups are rebuilt. Files on disk are never touched."""
    from .. import maintenance

    from ..locks import busy

    if busy():
        raise HTTPException(409, "Wait for the running job to finish first")
    try:
        return {"ok": True, "removed": maintenance.remove_root(root_id)}
    except KeyError:
        raise HTTPException(404, "Folder not found")


# ---------------------------------------------------------------- pipeline
class ScanIn(BaseModel):
    stages: list[str] | None = None


@router.post("/scan")
def start_scan(body: ScanIn):
    stages = body.stages or [k for k, _ in STAGES]
    bad = [s for s in stages if s not in dict(STAGES)]
    if bad:
        raise HTTPException(400, f"Unknown stage(s): {bad}")
    if not db.get().execute("SELECT 1 FROM roots").fetchone() and "scan" in stages:
        raise HTTPException(400, "Add a folder first")
    from ..locks import busy

    if busy("scan"):
        raise HTTPException(409, "Another job is running; wait for it to finish first")
    if not runner.start(stages):
        raise HTTPException(409, "A scan is already running")
    return runner.status()


@router.post("/scan/cancel")
def cancel_scan():
    runner.cancel()
    return runner.status()


@router.get("/scan/status")
def scan_status():
    return runner.status()


@router.get("/scan/history")
def scan_history(limit: int = 20):
    rows = db.get().execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) | {"summary": json.loads(r["summary"] or "null")} for r in rows]


# ---------------------------------------------------------------- settings
@router.get("/settings")
def get_settings():
    return {"values": db.get_settings(), "defaults": config.DEFAULT_SETTINGS, "data_dir": str(config.DATA_DIR), "workers": config.WORKERS}


@router.put("/settings")
def put_settings(values: dict[str, float]):
    return {"values": db.put_settings(values)}


# ---------------------------------------------------------------- stats
@router.get("/stats")
def stats():
    conn = db.get()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    by = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
    ok = "status='ok'"
    tiers = {
        r["tier"]: {"groups": r["n"], "files": r["files"], "reclaimable_bytes": r["rec"]}
        for r in conn.execute("SELECT tier, COUNT(*) n, SUM(size) files, SUM(reclaimable_bytes) rec FROM groups GROUP BY tier")
    }
    return {
        "total_files": one(f"SELECT COUNT(*) FROM files WHERE {ok}"),
        "total_bytes": one(f"SELECT COALESCE(SUM(size),0) FROM files WHERE {ok}"),
        "unique_images": one(f"SELECT COUNT(DISTINCT sha256) FROM files WHERE {ok}"),
        "errors": one("SELECT COUNT(*) FROM files WHERE status='error'"),
        "pending": one("SELECT COUNT(*) FROM files WHERE status='pending'"),
        "missing": one("SELECT COUNT(*) FROM files WHERE status='missing'"),
        "removed": one("SELECT COUNT(*) FROM files WHERE status='removed'"),
        "removed_bytes": one("SELECT COALESCE(SUM(size),0) FROM files WHERE status='removed'"),
        "embedded": one("SELECT COUNT(*) FROM images WHERE clip_row IS NOT NULL"),
        "images": one("SELECT COUNT(*) FROM images"),
        "ocr_done": one("SELECT COUNT(*) FROM ocr"),
        "with_text": one(f"SELECT COUNT(*) FROM files f JOIN ocr o ON o.sha256=f.sha256 WHERE f.{ok} AND o.n_words > 0"),
        "with_exif": one(f"SELECT COUNT(*) FROM files WHERE {ok} AND has_exif=1"),
        "with_date": one(f"SELECT COUNT(*) FROM files WHERE {ok} AND date_taken IS NOT NULL"),
        "with_gps": one(f"SELECT COUNT(*) FROM files WHERE {ok} AND gps_lat IS NOT NULL"),
        "formats": by(f"SELECT ext AS key, COUNT(*) AS count, SUM(size) AS bytes FROM files WHERE {ok} GROUP BY ext ORDER BY count DESC"),
        "years": by(
            f"SELECT COALESCE(substr(date_taken,1,4),'unknown') AS key, COUNT(*) AS count FROM files WHERE {ok} GROUP BY key ORDER BY key"
        ),
        "mtime_years": by(
            f"SELECT strftime('%Y', mtime, 'unixepoch') AS key, COUNT(*) AS count FROM files WHERE {ok} GROUP BY key ORDER BY key"
        ),
        "date_sources": by(
            f"SELECT COALESCE(date_source,'none') AS key, COUNT(*) AS count FROM files WHERE {ok} GROUP BY key ORDER BY count DESC"
        ),
        "cameras": by(
            f"SELECT TRIM(COALESCE(camera_make,'') || ' ' || camera_model) AS key, camera_model AS model, COUNT(*) AS count"
            f" FROM files WHERE {ok} AND camera_model IS NOT NULL GROUP BY camera_make, camera_model ORDER BY count DESC LIMIT 15"
        ),
        "sources": by(f"SELECT source_guess AS key, COUNT(*) AS count FROM files WHERE {ok} GROUP BY key ORDER BY count DESC"),
        "tiers": tiers,
        "decisions": by(
            f"SELECT d.decision AS key, COUNT(*) AS count FROM decisions d JOIN files f ON f.root_id=d.root_id AND f.rel_path=d.rel_path"
            f" WHERE f.{ok} GROUP BY d.decision"
        ),
    }


@router.get("/errors")
def errors(limit: int = 500):
    rows = db.get().execute(
        "SELECT f.id, r.path AS root, f.rel_path, f.size, f.error FROM files f JOIN roots r ON r.id=f.root_id"
        " WHERE f.status='error' ORDER BY f.rel_path LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- export
class ExportIn(BaseModel):
    kind: str = "csv"  # csv | json


@router.post("/export")
def export(body: ExportIn):
    conn = db.get()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if body.kind in ("csv", "json"):
        rows = conn.execute(
            """SELECT g.id AS group_id, g.tier, r.path AS root, f.rel_path, f.size, f.width, f.height, f.format,
                      f.date_taken, datetime(f.mtime,'unixepoch') AS modified, f.sha256,
                      (f.id = g.rep_file_id) AS is_best, d.decision
               FROM groups g JOIN group_members m ON m.group_id=g.id JOIN files f ON f.id=m.file_id
               JOIN roots r ON r.id=f.root_id
               LEFT JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path
               ORDER BY g.tier, g.id, is_best DESC"""
        ).fetchall()
        path = config.EXPORT_DIR / f"groups-{stamp}.{body.kind}"
        if body.kind == "csv":
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(rows[0].keys() if rows else ["group_id"])
                w.writerows([tuple(r) for r in rows])
        else:
            path.write_text(json.dumps([dict(r) for r in rows], indent=1, ensure_ascii=False), encoding="utf-8")
    else:
        raise HTTPException(400, "kind must be csv or json")
    return {"path": str(path), "name": path.name, "url": f"/api/export/{path.name}"}


@router.get("/export/{name}")
def download_export(name: str):
    p = (config.EXPORT_DIR / name).resolve()
    if p.parent != config.EXPORT_DIR.resolve() or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


@router.get("/health")
def health():
    return {"ok": True, "clip_rows": embed.store.count()}
