"""Similarity groups, keep/discard decisions."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import db, ocr
from .common import FILE_LIST_COLS, decisions_for, file_row

router = APIRouter(prefix="/api")

GROUP_SORTS = {"reclaimable": "g.reclaimable_bytes DESC", "size": "g.size DESC", "total": "g.total_bytes DESC", "id": "g.id"}


@router.get("/groups")
def list_groups(tier: str = "exact", sort: str = "reclaimable", offset: int = 0, limit: int = Query(60, le=300), undecided: bool = False):
    conn = db.get()
    where, args = "g.tier=?", [tier]
    if undecided:
        where += """ AND EXISTS (SELECT 1 FROM group_members m JOIN files f ON f.id=m.file_id
                     WHERE m.group_id=g.id AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.root_id=f.root_id AND d.rel_path=f.rel_path))"""
    total = conn.execute(f"SELECT COUNT(*) FROM groups g WHERE {where}", args).fetchone()[0]
    groups = [
        dict(r)
        for r in conn.execute(
            f"SELECT g.* FROM groups g WHERE {where} ORDER BY {GROUP_SORTS.get(sort, GROUP_SORTS['reclaimable'])}, g.id LIMIT ? OFFSET ?",
            args + [limit, offset],
        )
    ]
    for g in groups:
        rows = conn.execute(
            f"SELECT {FILE_LIST_COLS} FROM group_members m JOIN files f ON f.id=m.file_id WHERE m.group_id=?"
            " ORDER BY (f.id = ?) DESC, f.size DESC LIMIT 6",
            (g["id"], g["rep_file_id"]),
        ).fetchall()
        g["preview"] = [file_row(r) for r in rows]
        dec = decisions_for([x["id"] for x in g["preview"]])
        g["decided"] = conn.execute(
            """SELECT COUNT(*) FROM group_members m JOIN files f ON f.id=m.file_id
               JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path WHERE m.group_id=?""",
            (g["id"],),
        ).fetchone()[0]
        for x in g["preview"]:
            x["decision"] = dec.get(x["id"])
    return {"total": total, "items": groups}


@router.get("/groups/{group_id}")
def group_detail(group_id: int):
    conn = db.get()
    g = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    if not g:
        raise HTTPException(404)
    g = dict(g)
    members = [
        file_row(r)
        for r in conn.execute(
            f"SELECT {FILE_LIST_COLS}, f.lens, f.software, f.gps_lat, f.gps_lon, f.ctime, f.date_source"
            " FROM group_members m JOIN files f ON f.id=m.file_id WHERE m.group_id=?",
            (group_id,),
        )
    ]
    rep = next((m for m in members if m["id"] == g["rep_file_id"]), None)
    members.sort(key=lambda m: (m["id"] != g["rep_file_id"], -(m["size"] or 0)))
    dec = decisions_for([m["id"] for m in members])
    shas = sorted({m["sha256"] for m in members})
    pairs = {}
    if len(shas) > 1:
        q = ",".join("?" * len(shas))
        for p in conn.execute(f"SELECT * FROM pairs WHERE a IN ({q}) AND b IN ({q})", shas + shas):
            pairs[(p["a"], p["b"])] = dict(p)
    texts = {}
    for sha in shas:
        o = ocr.get(sha)
        texts[sha] = o["text"] if o else None
    for m in members:
        m["ocr_text"] = texts.get(m["sha256"])
        m["decision"] = dec.get(m["id"])
        m["is_rep"] = m["id"] == g["rep_file_id"]
        if rep and m["sha256"] == rep["sha256"]:
            m["vs_rep"] = {"exact": True}
        elif rep:
            k = tuple(sorted((m["sha256"], rep["sha256"])))
            m["vs_rep"] = pairs.get(k)
    g["members"] = members
    g["pairs"] = list(pairs.values())
    return g


class Decisions(BaseModel):
    file_ids: list[int]
    decision: str  # keep | discard | clear


def _apply(file_ids: list[int], decision: str) -> int:
    if decision not in ("keep", "discard", "clear"):
        raise HTTPException(400, "decision must be keep, discard or clear")
    conn = db.get()
    with db.tx() as c:
        for s in range(0, len(file_ids), 900):
            chunk = file_ids[s : s + 900]
            rows = conn.execute(f"SELECT root_id, rel_path FROM files WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            if decision == "clear":
                c.executemany("DELETE FROM decisions WHERE root_id=? AND rel_path=?", [(r["root_id"], r["rel_path"]) for r in rows])
            else:
                c.executemany(
                    "INSERT INTO decisions(root_id, rel_path, decision, updated_at) VALUES(?,?,?,?)"
                    " ON CONFLICT(root_id, rel_path) DO UPDATE SET decision=excluded.decision, updated_at=excluded.updated_at",
                    [(r["root_id"], r["rel_path"], decision, time.time()) for r in rows],
                )
    return len(file_ids)


@router.put("/decisions")
def put_decisions(body: Decisions):
    return {"updated": _apply(body.file_ids, body.decision)}


@router.post("/groups/{group_id}/auto-decide")
def auto_decide(group_id: int):
    """Keep the best file (highest resolution, real EXIF date, largest), mark the rest discard."""
    conn = db.get()
    g = conn.execute("SELECT rep_file_id FROM groups WHERE id=?", (group_id,)).fetchone()
    if not g:
        raise HTTPException(404)
    ids = [r[0] for r in conn.execute("SELECT file_id FROM group_members WHERE group_id=?", (group_id,))]
    _apply([g["rep_file_id"]], "keep")
    _apply([i for i in ids if i != g["rep_file_id"]], "discard")
    return {"kept": g["rep_file_id"], "discarded": len(ids) - 1}


class BulkAuto(BaseModel):
    tier: str


AUTO_TIERS = ("exact", "duplicate")


def decide_groups(tiers: list[str], dry_run: bool = False) -> dict:
    """Keep the best copy and discard the rest in every *undecided* group of the given tiers.

    Only exact and duplicate tiers are allowed (near/similar/text need a human). Decisions that existed
    before this call are never overwritten, and a group containing any of them is left alone.
    Duplicate groups (supersets of exact groups) are handled first, so one best copy is kept per cluster.
    With dry_run nothing is written. Returns counts and the file ids marked.
    """
    bad = [t for t in tiers if t not in AUTO_TIERS]
    if bad:
        raise ValueError(f"Automatic decisions are only allowed for exact and duplicate groups (got {bad})")
    conn = db.get()
    q = ",".join("?" * len(tiers)) or "''"
    groups = conn.execute(f"SELECT id, rep_file_id FROM groups WHERE tier IN ({q}) ORDER BY tier='exact', id", tiers).fetchall()
    members = {g["id"]: [r[0] for r in conn.execute("SELECT file_id FROM group_members WHERE group_id=?", (g["id"],))] for g in groups}
    pre = set(decisions_for(sorted({i for ids in members.values() for i in ids})))
    now: dict[int, str] = {}
    n = 0
    for g in groups:
        ids = members[g["id"]]
        if pre.intersection(ids) or all(i in now for i in ids):
            continue
        rep = g["rep_file_id"]
        if now.get(rep) == "discard":
            continue                         # never let a group end up fully discarded
        now.setdefault(rep, "keep")
        for i in ids:
            now.setdefault(i, "discard")
        n += 1
    kept = [i for i, d in now.items() if d == "keep"]
    discarded = [i for i, d in now.items() if d == "discard"]
    if not dry_run:
        _apply(kept, "keep")
        _apply(discarded, "discard")
    return {"groups_decided": n, "kept": kept, "discarded": discarded}


def preview_decide(tiers: list[str]) -> dict:
    """What decide_groups would do, without writing anything."""
    return decide_groups(tiers, dry_run=True)


@router.post("/groups/auto-decide-tier")
def auto_decide_tier(body: BulkAuto):
    """Auto-decide every *undecided* group of a tier. Existing decisions are never overwritten."""
    try:
        r = decide_groups([body.tier])
    except ValueError:
        raise HTTPException(400, "Bulk auto-decide is only offered for exact and duplicate tiers")
    return {"groups_decided": r["groups_decided"]}
