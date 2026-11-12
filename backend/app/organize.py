"""Organize: write an organized COPY of the library into a new folder. Originals are never modified.

Layouts
  flat    every selected photo in one folder
  groups  one sub-folder per similarity group (+ "_unique" for photos in no group)

Naming
  keep        original names; a clash inside a folder gets "_2", "_3", ... (case-insensitive)
  duplicates  photos that belong to a group are renamed with the pattern, others keep their name
  all         every photo is renamed with the pattern
Patterns: date (2019-08-12_15-30-12), date_name (2019-08-12_15-30-12_IMG_1234), group (g0003_01_IMG_1234)
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from . import config, db
from .grouping import _rep_key

TIERS = ["exact", "duplicate", "near", "similar", "text"]
FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "BMP": "bmp", "TIFF": "tif", "HEIF": "heic", "AVIF": "avif", "MPO": "jpg"}
_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


class Options(BaseModel):
    dest: str
    scope: str = "not_discarded"            # all | grouped | not_discarded
    layout: str = "groups"                  # flat | groups
    tiers: list[str] = ["exact", "duplicate", "near"]
    one_per_exact: bool = False             # copy only the best of byte-identical files
    rename: str = "duplicates"              # keep | duplicates | all
    pattern: str = "date_name"              # date | date_name | group


def _clean(name: str, limit: int = 120) -> str:
    name = _BAD.sub("_", name).strip(" .")
    return (name or "photo")[:limit]


def _ext(f) -> str:
    ext = (f["ext"] or "").lower()
    from .decode import IMAGE_EXTS

    if ext in IMAGE_EXTS:
        return "jpg" if ext in ("jpeg", "jpe", "jfif", "pjpeg") else ext
    return FORMAT_EXT.get(f["format"] or "", ext or "img")


def _stamp(f) -> str:
    if f["date_taken"]:
        try:
            return datetime.fromisoformat(f["date_taken"]).strftime("%Y-%m-%d_%H-%M-%S")
        except ValueError:
            pass
    return datetime.fromtimestamp(f["mtime"] or 0).strftime("%Y-%m-%d_%H-%M-%S")


def validate_dest(dest: str) -> Path:
    from .paths import check_outside_library

    return check_outside_library(dest)


def build_plan(o: Options) -> dict:
    if o.scope not in ("all", "grouped", "not_discarded") or o.layout not in ("flat", "groups"):
        raise ValueError("Invalid scope or layout")
    if o.rename not in ("keep", "duplicates", "all") or o.pattern not in ("date", "date_name", "group"):
        raise ValueError("Invalid naming options")
    tiers = [t for t in o.tiers if t in TIERS]
    conn = db.get()
    files = {
        r["id"]: r
        for r in conn.execute(
            """SELECT f.id, f.root_id, r.path AS root, f.rel_path, f.name, f.ext, f.format, f.size, f.mtime,
                      f.width, f.height, f.has_exif, f.date_source, f.date_taken, f.sha256, d.decision
               FROM files f JOIN roots r ON r.id=f.root_id
               LEFT JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path
               WHERE f.status='ok'"""
        )
    }

    # --- groups: union of the selected tiers' groups (a photo may be in several -> merge)
    parent: dict[int, int] = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    tier_of: dict[int, str] = {}
    if tiers:
        q = ",".join("?" * len(tiers))
        members = defaultdict(list)
        gtier = {}
        for r in conn.execute(f"SELECT g.id, g.tier, m.file_id FROM groups g JOIN group_members m ON m.group_id=g.id WHERE g.tier IN ({q})", tiers):
            members[r["id"]].append(r["file_id"])
            gtier[r["id"]] = r["tier"]
        order = {t: i for i, t in enumerate(TIERS)}
        for gid, ids in members.items():
            ids = [i for i in ids if i in files]
            for i in ids:
                if i not in tier_of or order[gtier[gid]] < order[tier_of[i]]:
                    tier_of[i] = gtier[gid]
            for i in ids[1:]:
                parent[find(i)] = find(ids[0])
    comp: dict[int, list[int]] = defaultdict(list)
    for fid in tier_of:
        comp[find(fid)].append(fid)
    groups = [sorted(ids, key=lambda i: _rep_key(files[i]), reverse=True) for ids in comp.values() if len(ids) > 1]
    groups.sort(key=lambda g: (-len(g), files[g[0]]["date_taken"] or "", files[g[0]]["rel_path"]))
    group_of = {fid: (gi + 1) for gi, g in enumerate(groups) for fid in g}

    # --- selection
    def selected(fid):
        f = files[fid]
        if o.scope == "grouped" and fid not in group_of:
            return False
        if o.scope == "not_discarded" and f["decision"] == "discard":
            return False
        return True

    chosen = [fid for fid in files if selected(fid)]
    if o.one_per_exact:
        best: dict[str, int] = {}
        for fid in chosen:
            sha = files[fid]["sha256"]
            if sha not in best or _rep_key(files[fid]) > _rep_key(files[best[sha]]):
                best[sha] = fid
        keep = set(best.values())
        chosen = [fid for fid in chosen if fid in keep]
    chosen_set = set(chosen)

    # --- folder + name per file
    plan = []
    used: dict[str, set[str]] = defaultdict(set)
    idx_in_group: dict[int, int] = {}
    for g in groups:
        n = 0
        for fid in g:
            if fid in chosen_set:
                n += 1
                idx_in_group[fid] = n

    def folder_for(fid) -> str:
        if o.layout == "flat":
            return ""
        gi = group_of.get(fid)
        if gi is None:
            return "_unique"
        rep = files[groups[gi - 1][0]]
        return f"g{gi:04d}_{_clean(Path(rep['name']).stem, 40)}_({len([x for x in groups[gi - 1] if x in chosen_set])})"

    def ordered(fid):
        gi = group_of.get(fid)
        return (0 if gi else 1, gi or 0, idx_in_group.get(fid, 0), files[fid]["rel_path"])

    for fid in sorted(chosen, key=ordered):
        f = files[fid]
        folder = folder_for(fid)
        ext = _ext(f)
        stem = Path(f["name"]).stem
        in_group = fid in group_of
        rename = o.rename == "all" or (o.rename == "duplicates" and in_group)
        if not rename:
            base = stem
        elif o.pattern == "date":
            base = _stamp(f)
        elif o.pattern == "group" and in_group:
            base = f"g{group_of[fid]:04d}_{idx_in_group[fid]:02d}_{stem}"
        else:
            base = f"{_stamp(f)}_{stem}"
        base = _clean(base)
        name, n = f"{base}.{ext}", 1
        while name.lower() in used[folder]:
            n += 1
            name = f"{base}_{n}.{ext}"
        used[folder].add(name.lower())
        plan.append(
            {
                "file_id": fid,
                "src": str(Path(f["root"]) / f["rel_path"]),
                "dst": f"{folder}/{name}" if folder else name,
                "group": group_of.get(fid),
                "tier": tier_of.get(fid),
                "is_best": bool(in_group and idx_in_group.get(fid) == 1),
                "decision": f["decision"],
                "size": f["size"] or 0,
                "renamed": name != f["name"],
            }
        )
    return {
        "items": plan,
        "files": len(plan),
        "bytes": sum(p["size"] for p in plan),
        "folders": len({p["dst"].rsplit("/", 1)[0] for p in plan if "/" in p["dst"]}) or (1 if plan else 0),
        "groups": len({p["group"] for p in plan if p["group"]}),
        "renamed": sum(1 for p in plan if p["renamed"]),
    }


class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.state: dict = {"running": False}

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive()) or bool(self.state.get("running"))

    def _prepare(self, o: Options) -> tuple[Path, list[dict]]:
        if self.running():
            raise RuntimeError("An organize job is already running")
        dest = validate_dest(o.dest)
        plan = build_plan(o)
        if not plan["items"]:
            raise ValueError("No photos match these options")
        free = shutil.disk_usage(dest).free
        if plan["bytes"] > free * 0.98:
            raise ValueError(f"Not enough free space: need {plan['bytes'] / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
        out = dest / f"Organized {time.strftime('%Y-%m-%d %H%M%S')}"
        n = 2
        while out.exists():
            out = dest / f"Organized {time.strftime('%Y-%m-%d %H%M%S')} ({n})"
            n += 1
        out.mkdir()
        self.cancel.clear()
        self.state = {"running": True, "out": str(out), "total": plan["files"], "done": 0, "bytes_total": plan["bytes"],
                      "bytes_done": 0, "errors": [], "started": time.time(), "finished": None, "cancelled": False}
        return out, plan["items"]

    def start(self, o: Options) -> dict:
        from .locks import ensure_idle

        ensure_idle("organize")
        out, items = self._prepare(o)
        self.thread = threading.Thread(target=self._run, args=(out, items), daemon=True, name="organize")
        self.thread.start()
        return self.status()

    def run_sync(self, o: Options) -> dict:
        """Run in the calling thread (used by Auto Process). Returns the final status."""
        out, items = self._prepare(o)
        self._run(out, items)
        return self.status()

    def _run(self, out: Path, items: list[dict]) -> None:
        rows = []
        try:
            for it in items:
                if self.cancel.is_set():
                    with self.lock:
                        self.state["cancelled"] = True
                    break
                dst = out / it["dst"]
                status = "copied"
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        raise FileExistsError(str(dst))       # never overwrite
                    tmp = dst.with_name(dst.name + ".partial")
                    shutil.copy2(it["src"], tmp)              # read-only on the source; keeps mtime
                    os.replace(tmp, dst)
                except Exception as e:  # noqa: BLE001
                    status = f"error: {type(e).__name__}: {e}"
                    with self.lock:
                        self.state["errors"].append({"src": it["src"], "error": status[7:][:300]})
                rows.append([it["src"], it["dst"], it["group"] or "", it["tier"] or "", "yes" if it["is_best"] else "",
                             it["decision"] or "", it["size"], status])
                with self.lock:
                    self.state["done"] += 1
                    self.state["bytes_done"] += it["size"]
        finally:
            with open(out / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["source", "destination", "group", "tier", "best_copy", "decision", "bytes", "status"])
                w.writerows(rows)
            with self.lock:
                self.state["running"] = False
                self.state["finished"] = time.time()


job = Job()
