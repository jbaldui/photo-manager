"""Photo listing, detail, thumbnails, previews, similar photos and text search."""
from __future__ import annotations

import io
import mimetypes

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .. import config, db, decode, embed, ocr
from .common import FILE_LIST_COLS, abs_path, decisions_for, file_row, full_file

router = APIRouter(prefix="/api")

SORTS = {
    "date_taken": "COALESCE(f.date_taken, datetime(f.mtime, 'unixepoch'))",
    "mtime": "f.mtime",
    "size": "f.size",
    "name": "f.name COLLATE NOCASE",
    "path": "f.rel_path COLLATE NOCASE",
    "resolution": "(COALESCE(f.width,0) * COALESCE(f.height,0))",
}
BROWSER_FORMATS = {"JPEG", "PNG", "GIF", "WEBP", "BMP"}


@router.get("/photos")
def list_photos(
    q: str | None = None,
    ext: str | None = None,
    year: str | None = None,
    camera: str | None = None,
    source: str | None = None,
    status: str = "ok",
    has_date: bool | None = None,
    tier: str | None = None,
    decision: str | None = None,
    text: str | None = None,
    has_text: bool | None = None,
    root_id: int | None = None,
    person_id: int | None = None,
    sort: str = "date_taken",
    order: str = "desc",
    offset: int = 0,
    limit: int = Query(200, le=1000),
):
    where, args = ["1=1"], []
    if status != "all":
        where.append("f.status=?")
        args.append(status)
    if q:
        where.append("f.rel_path LIKE ?")
        args.append(f"%{q}%")
    if ext:
        where.append("f.ext=?")
        args.append(ext.lower())
    if year:
        if year == "unknown":
            where.append("f.date_taken IS NULL")
        else:
            where.append("substr(f.date_taken,1,4)=?")
            args.append(year)
    if camera:
        where.append("f.camera_model=?")
        args.append(camera)
    if source:
        where.append("f.source_guess=?")
        args.append(source)
    if has_date is not None:
        where.append("f.date_taken IS NOT NULL" if has_date else "f.date_taken IS NULL")
    if tier:
        where.append("f.id IN (SELECT m.file_id FROM group_members m JOIN groups g ON g.id=m.group_id WHERE g.tier=?)")
        args.append(tier)
    if decision:
        cond = "EXISTS" if decision != "undecided" else "NOT EXISTS"
        sub = "SELECT 1 FROM decisions d WHERE d.root_id=f.root_id AND d.rel_path=f.rel_path"
        if decision != "undecided":
            sub += " AND d.decision=?"
            args.append(decision)
        where.append(f"{cond} ({sub})")
    if text:
        fq = ocr.fts_query(text)
        if fq:
            where.append("f.sha256 IN (SELECT sha256 FROM ocr_fts WHERE ocr_fts MATCH ?)")
            args.append(fq)
    if has_text is not None:
        where.append(("" if has_text else "NOT ") + "EXISTS (SELECT 1 FROM ocr o WHERE o.sha256=f.sha256 AND o.n_words > 0)")
    if person_id:
        where.append("f.sha256 IN (SELECT sha256 FROM faces WHERE person_id=?)")
        args.append(person_id)
    if root_id:
        where.append("f.root_id=?")
        args.append(root_id)
    sort_sql = SORTS.get(sort, SORTS["date_taken"])
    direction = "ASC" if order == "asc" else "DESC"
    conn = db.get()
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM files f WHERE {w}", args).fetchone()[0]
    rows = conn.execute(
        f"SELECT {FILE_LIST_COLS} FROM files f WHERE {w} ORDER BY {sort_sql} {direction}, f.id LIMIT ? OFFSET ?",
        args + [limit, offset],
    ).fetchall()
    items = [file_row(r) for r in rows]
    dec = decisions_for([i["id"] for i in items])
    for i in items:
        i["decision"] = dec.get(i["id"])
    return {"total": total, "offset": offset, "items": items}


@router.get("/photos/{file_id}")
def photo_detail(file_id: int):
    d = full_file(file_id)
    if not d:
        raise HTTPException(404)
    conn = db.get()
    d["groups"] = [
        dict(r)
        for r in conn.execute(
            "SELECT g.id, g.tier, g.size FROM group_members m JOIN groups g ON g.id=m.group_id WHERE m.file_id=?",
            (file_id,),
        )
    ]
    d["same_content"] = [
        dict(r)
        for r in conn.execute(
            "SELECT id, rel_path, mtime, size FROM files WHERE sha256=? AND id<>? AND status='ok'", (d["sha256"], file_id)
        )
    ] if d.get("sha256") else []
    d["ocr"] = ocr.get(d["sha256"]) if d.get("sha256") else None
    img = conn.execute("SELECT phash, dhash, whash, colorhash, clip_row FROM images WHERE sha256=?", (d.get("sha256"),)).fetchone()
    if img:
        from ..hashing import to_unsigned64

        d["hashes"] = {
            "sha256": d["sha256"],
            "phash": f"{to_unsigned64(img['phash']):016x}",
            "dhash": f"{to_unsigned64(img['dhash']):016x}",
            "whash": f"{to_unsigned64(img['whash']):016x}",
            "colorhash": img["colorhash"],
            "clip_embedded": img["clip_row"] is not None,
        }
    return d


@router.get("/photos/{file_id}/thumb")
def thumb(file_id: int):
    r = db.get().execute(
        "SELECT i.thumb FROM files f JOIN images i ON i.sha256=f.sha256 WHERE f.id=?", (file_id,)
    ).fetchone()
    if not r or not r["thumb"]:
        raise HTTPException(404)
    return FileResponse(config.THUMB_DIR / r["thumb"], media_type="image/webp", headers={"Cache-Control": "max-age=86400"})


@router.get("/photos/{file_id}/original")
def original(file_id: int):
    p = abs_path(file_id)
    if not p or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")


@router.get("/photos/{file_id}/preview")
def preview(file_id: int, size: int = 1600):
    """Browser-friendly large preview (converts HEIC/RAW/TIFF on the fly; originals untouched)."""
    p = abs_path(file_id)
    if not p or not p.is_file():
        raise HTTPException(404)
    try:
        img = decode.open_image(p)
        if img.format in BROWSER_FORMATS and img.format != "GIF" and max(img.size) <= size and not img.getexif().get(274, 1) > 1:
            return FileResponse(p, media_type=Image_mime(img.format))
        rgb = decode.to_working_rgb(img, min(size, 4096))
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=88)
        return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    except Exception as e:
        raise HTTPException(415, f"Cannot render: {e}")


def Image_mime(fmt: str) -> str:
    return {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp", "GIF": "image/gif"}[fmt]


def _files_for_shas(scored: list[tuple[str, float]], exclude_sha: str | None = None) -> list[dict]:
    if not scored:
        return []
    conn = db.get()
    shas = [s for s, _ in scored]
    rows = conn.execute(
        f"SELECT {FILE_LIST_COLS} FROM files f WHERE f.status='ok' AND f.sha256 IN ({','.join('?' * len(shas))})"
        " ORDER BY f.id",
        shas,
    ).fetchall()
    first: dict[str, dict] = {}
    count: dict[str, int] = {}
    for r in rows:
        count[r["sha256"]] = count.get(r["sha256"], 0) + 1
        first.setdefault(r["sha256"], file_row(r))
    out = []
    for sha, score in scored:
        if sha in first and sha != exclude_sha:
            item = first[sha] | {"score": round(score, 4), "copies": count[sha]}
            out.append(item)
    return out


@router.get("/photos/{file_id}/similar")
def similar(file_id: int, limit: int = 24):
    conn = db.get()
    r = conn.execute("SELECT sha256 FROM files WHERE id=?", (file_id,)).fetchone()
    if not r or not r["sha256"]:
        raise HTTPException(404)
    sha = r["sha256"]
    items = _files_for_shas(embed.similar_to(sha, limit), exclude_sha=sha)
    pair = {}
    for p in conn.execute("SELECT * FROM pairs WHERE a=? OR b=?", (sha, sha)):
        other = p["b"] if p["a"] == sha else p["a"]
        pair[other] = dict(p)
    for it in items:
        it["pair"] = pair.get(it["sha256"])
    return {"items": items}


@router.get("/search")
def search(q: str, mode: str = "text", limit: int = 120):
    """mode=text: words found in the image (OCR, accent-insensitive, "quoted phrases", prefix on last word).
    mode=clip: describe the visual content ("dog on a beach")."""
    if not q.strip():
        return {"items": []}
    if mode == "clip":
        if embed.store.count() == 0:
            raise HTTPException(409, "No CLIP embeddings yet - run a scan with the CLIP stage first.")
        return {"items": _files_for_shas(embed.search_text(q, limit))}
    if not db.get().execute("SELECT 1 FROM ocr LIMIT 1").fetchone():
        raise HTTPException(409, "No OCR results yet - run a scan with the OCR stage first.")
    fq = ocr.fts_query(q)
    if not fq:
        return {"items": []}
    rows = db.get().execute(
        "SELECT sha256, bm25(ocr_fts) AS rank, snippet(ocr_fts, 1, '[[', ']]', ' … ', 16) AS snip"
        " FROM ocr_fts WHERE ocr_fts MATCH ? ORDER BY rank LIMIT ?",
        (fq, limit),
    ).fetchall()
    snips = {r["sha256"]: r["snip"] for r in rows}
    items = _files_for_shas([(r["sha256"], -float(r["rank"])) for r in rows])
    for it in items:
        it["snippet"] = snips.get(it["sha256"])
    total = db.get().execute("SELECT COUNT(*) FROM ocr_fts WHERE ocr_fts MATCH ?", (fq,)).fetchone()[0]
    return {"items": items, "total": total}
