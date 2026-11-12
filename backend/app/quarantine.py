"""Quarantine: move photos you discarded out of your library, with one-click undo.

Safety rules
- Files are only ever *moved* (never deleted, never overwritten) into
  <quarantine folder>/<batch timestamp>/<scanned folder name>/<original relative path>.
- Every move is written to the `removals` table *before* it happens, so a crash can never lose track of a file.
- The last remaining copy of a group is never moved: if every member of a group would go, its best copy stays.
- A file that changed on disk since the last scan (size / mtime) is skipped.
- Moved files keep their `files` row with status 'removed', so restoring them needs no re-analysis.
"""
from __future__ import annotations

import csv
import errno
import hashlib
import os
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel

from . import config, db, grouping
from .grouping import _rep_key
from .paths import check_outside_library

LAST_DEST_KEY = "quarantine_dest"


class RemovalOptions(BaseModel):
    dest: str | None = None              # quarantine folder (default: last used, else ~/Photo Manager Quarantine)
    scope: str = "discarded"             # discarded | files
    file_ids: list[int] = []             # scope=files, or restricts scope=discarded
    group_id: int | None = None          # restrict to one group's discards
    reason: str = "manual"


class _GroupCtx:
    def __init__(self, job: "Job") -> None:
        self.job = job

    def cancelled(self) -> bool:
        return False

    def message(self, msg: str) -> None:
        pass

    def update(self, processed, total, msg=None) -> None:
        pass


def default_dest() -> str:
    return db.kv_get(LAST_DEST_KEY) or str(config.DEFAULT_QUARANTINE)


def _validate_dest(dest: str, create: bool) -> tuple[Path, bool]:
    """Returns (resolved folder, exists). A missing folder is validated through its parent."""
    p = Path(dest).expanduser()
    if p.exists() or create:
        return check_outside_library(p, create=create, what="Quarantine"), True
    parent = p.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    check_outside_library(parent, what="Quarantine")
    return p.resolve(), False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(o: RemovalOptions, create: bool = False) -> dict:
    if o.scope not in ("discarded", "files"):
        raise ValueError("scope must be 'discarded' or 'files'")
    dest, exists = _validate_dest(o.dest or default_dest(), create)
    conn = db.get()
    cols = ("f.id, f.root_id, r.path AS root, f.rel_path, f.name, f.size, f.mtime, f.sha256, f.width, f.height,"
            " f.has_exif, f.date_source")
    if o.scope == "discarded":
        sql = (f"SELECT {cols} FROM files f JOIN roots r ON r.id=f.root_id"
               " JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path"
               " WHERE f.status='ok' AND d.decision='discard'")
        args: list = []
        if o.group_id is not None:
            sql += " AND f.id IN (SELECT file_id FROM group_members WHERE group_id=?)"
            args.append(o.group_id)
        rows = conn.execute(sql, args).fetchall()
        if o.file_ids:
            wanted = set(o.file_ids)
            rows = [r for r in rows if r["id"] in wanted]
    else:
        rows = []
        for s in range(0, len(o.file_ids), 900):
            chunk = o.file_ids[s : s + 900]
            rows += conn.execute(
                f"SELECT {cols} FROM files f JOIN roots r ON r.id=f.root_id WHERE f.status='ok' AND f.id IN ({','.join('?' * len(chunk))})",
                chunk,
            ).fetchall()
    cand = {r["id"]: r for r in rows}

    # --- never move every copy of a group: keep the best candidate when nothing else would survive
    blocked: list[dict] = []
    if cand:
        ids = list(cand)
        group_ids: set[int] = set()
        for s in range(0, len(ids), 900):
            chunk = ids[s : s + 900]
            group_ids.update(r[0] for r in conn.execute(
                f"SELECT DISTINCT group_id FROM group_members WHERE file_id IN ({','.join('?' * len(chunk))})", chunk))
        members: dict[int, list] = defaultdict(list)
        gl = sorted(group_ids)
        for s in range(0, len(gl), 900):
            chunk = gl[s : s + 900]
            for r in conn.execute(
                f"""SELECT m.group_id, f.id, r.path AS root, f.rel_path, f.size, f.mtime, f.width, f.height, f.has_exif, f.date_source
                    FROM group_members m JOIN files f ON f.id=m.file_id JOIN roots r ON r.id=f.root_id
                    WHERE f.status='ok' AND m.group_id IN ({','.join('?' * len(chunk))})""",
                chunk,
            ):
                members[r["group_id"]].append(r)
        for gid in gl:
            survivors = [m for m in members[gid] if m["id"] not in cand and os.path.exists(os.path.join(m["root"], m["rel_path"]))]
            if survivors:
                continue
            in_batch = [cand[m["id"]] for m in members[gid] if m["id"] in cand]
            if not in_batch:
                continue
            best = max(in_batch, key=_rep_key)
            cand.pop(best["id"])
            blocked.append({"file_id": best["id"], "rel_path": best["rel_path"], "reason": "every copy in its group was marked discard; the best copy is kept"})

    # --- destinations
    root_names: dict[str, str] = {}
    for r in conn.execute("SELECT id, path FROM roots ORDER BY id"):
        name = Path(r["path"]).name or f"root{r['id']}"
        if name in root_names.values():
            name = f"{name}_{r['id']}"
        root_names[r["path"]] = name
    items = [
        {
            "file_id": r["id"], "root": r["root"], "rel_path": r["rel_path"], "src": str(Path(r["root"]) / r["rel_path"]),
            "qrel": str(Path(root_names.get(r["root"], "root")) / r["rel_path"]), "size": r["size"] or 0,
            "mtime": r["mtime"], "sha256": r["sha256"],
        }
        for r in sorted(cand.values(), key=lambda r: (r["root"], r["rel_path"]))
    ]
    same_device = True
    probe = dest if exists else next((p for p in dest.parents if p.exists()), Path("/"))
    try:
        dev = os.stat(probe).st_dev
        same_device = all(os.stat(root).st_dev == dev for root in {i["root"] for i in items} if os.path.isdir(root))
    except OSError:
        pass
    return {
        "items": items,
        "files": len(items),
        "bytes": sum(i["size"] for i in items),
        "blocked": blocked,
        "dest": str(dest),
        "dest_exists": exists,
        "same_device": same_device,
        "free_bytes": shutil.disk_usage(probe).free,
    }


def _move(src: Path, dst: Path, sha: str | None) -> None:
    """Move without ever overwriting. Same filesystem: rename. Otherwise copy, verify, then unlink."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(f"already exists: {dst}")
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = dst.with_name(dst.name + ".partial")
    shutil.copy2(src, tmp)
    if sha and _sha256(tmp) != sha:
        tmp.unlink(missing_ok=True)
        raise OSError("copy verification failed (SHA-256 mismatch); original left in place")
    os.replace(tmp, dst)
    os.unlink(src)


class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.state: dict = {"running": False}

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive()) or bool(self.state.get("running"))

    def status(self) -> dict:
        with self.lock:
            return {**self.state, "errors": list(self.state.get("errors", []))}

    def _begin(self, mode: str, total: int, bytes_total: int, batch_id: int) -> None:
        self.cancel.clear()
        self.state = {"running": True, "mode": mode, "batch_id": batch_id, "phase": "moving", "total": total, "done": 0,
                      "moved": 0, "skipped": 0, "conflicts": 0, "bytes_total": bytes_total, "bytes_done": 0, "errors": [],
                      "started": time.time(), "finished": None, "cancelled": False}

    # ------------------------------------------------------------- move to quarantine
    def _prepare(self, o: RemovalOptions) -> tuple[int, Path, list[dict]]:
        if self.running():
            raise RuntimeError("A quarantine job is already running")
        plan = build_plan(o, create=True)
        if not plan["items"]:
            raise ValueError("Nothing to move: no photos marked discard" + (" (the best copy of each group is always kept)" if plan["blocked"] else ""))
        dest = Path(plan["dest"])
        batch_dir = dest / time.strftime("%Y-%m-%d %H%M%S")
        n = 2
        while batch_dir.exists():
            batch_dir = dest / f"{time.strftime('%Y-%m-%d %H%M%S')} ({n})"
            n += 1
        batch_dir.mkdir()
        db.kv_put(LAST_DEST_KEY, str(dest))
        with db.tx() as c:
            bid = c.execute(
                "INSERT INTO removal_batches(created, dest, reason, files, bytes, state) VALUES(?,?,?,?,?, 'running')",
                (time.time(), str(batch_dir), o.reason, 0, 0),
            ).lastrowid
        self._begin("move", plan["files"], plan["bytes"], bid)
        self.state["dest"] = str(batch_dir)
        self.state["blocked"] = len(plan["blocked"])
        return bid, batch_dir, plan["items"]

    def start(self, o: RemovalOptions) -> dict:
        from .locks import ensure_idle

        ensure_idle("quarantine")
        bid, batch_dir, items = self._prepare(o)
        self.thread = threading.Thread(target=self._run_move, args=(bid, batch_dir, items), daemon=True, name="quarantine")
        self.thread.start()
        return self.status()

    def run_sync(self, o: RemovalOptions) -> dict:
        bid, batch_dir, items = self._prepare(o)
        self._run_move(bid, batch_dir, items)
        return self.status()

    def _err(self, src: str, msg: str) -> None:
        with self.lock:
            if len(self.state["errors"]) < 1000:
                self.state["errors"].append({"src": src, "error": msg[:300]})

    def _run_move(self, bid: int, batch_dir: Path, items: list[dict]) -> None:
        moved = moved_bytes = 0
        final = "done"
        try:
            for it in items:
                if self.cancel.is_set():
                    final = "cancelled"
                    with self.lock:
                        self.state["cancelled"] = True
                    break
                src, dst = Path(it["src"]), batch_dir / it["qrel"]
                status, err = "moved", None
                try:
                    st = src.stat()
                    if st.st_size != it["size"] or abs(st.st_mtime - (it["mtime"] or 0)) > 1e-6:
                        status, err = "skipped", "changed since the last scan; rescan first"
                except FileNotFoundError:
                    status, err = "skipped", "file no longer exists"
                with db.tx() as c:
                    rid = c.execute(
                        "INSERT INTO removals(batch_id, file_id, root_path, rel_path, quarantine_path, sha256, size, mtime, status, error)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, it["file_id"], it["root"], it["rel_path"], str(dst), it["sha256"], it["size"], it["mtime"],
                         "pending" if status == "moved" else status, err),
                    ).lastrowid
                if status == "moved":
                    try:
                        _move(src, dst, it["sha256"])
                    except Exception as e:  # noqa: BLE001
                        status, err = "error", f"{type(e).__name__}: {e}"
                    with db.tx() as c:
                        c.execute("UPDATE removals SET status=?, error=? WHERE id=?", (status, err, rid))
                        if status == "moved":
                            c.execute("UPDATE files SET status='removed' WHERE id=?", (it["file_id"],))
                if status == "moved":
                    moved += 1
                    moved_bytes += it["size"]
                elif err:
                    self._err(it["src"], err)
                with self.lock:
                    self.state["done"] += 1
                    self.state["bytes_done"] += it["size"]
                    self.state["moved"] = moved
                    self.state["skipped"] += status == "skipped"
        except Exception as e:  # noqa: BLE001
            final = "error"
            self._err("", f"{type(e).__name__}: {e}")
        finally:
            self._finish(bid, final, moved, moved_bytes, batch_dir)

    def _finish(self, bid: int, final: str, moved: int, moved_bytes: int, batch_dir: Path | None) -> None:
        try:
            with self.lock:
                self.state["phase"] = "regrouping"
            grouping.build_groups(_GroupCtx(self))
            if batch_dir is not None:
                with db.tx() as c:
                    c.execute("UPDATE removal_batches SET files=?, bytes=?, state=? WHERE id=?", (moved, moved_bytes, final, bid))
                _write_manifest(bid, batch_dir)
        finally:
            with self.lock:
                self.state["running"] = False
                self.state["phase"] = "done"
                self.state["finished"] = time.time()

    # ------------------------------------------------------------- undo
    def start_restore(self, bid: int) -> dict:
        from .locks import ensure_idle

        ensure_idle("quarantine")
        rows = self._prepare_restore(bid)
        self.thread = threading.Thread(target=self._run_restore, args=(bid, rows), daemon=True, name="restore")
        self.thread.start()
        return self.status()

    def restore_sync(self, bid: int) -> dict:
        self._run_restore(bid, self._prepare_restore(bid))
        return self.status()

    def _prepare_restore(self, bid: int) -> list:
        if self.running():
            raise RuntimeError("A quarantine job is already running")
        conn = db.get()
        if not conn.execute("SELECT 1 FROM removal_batches WHERE id=?", (bid,)).fetchone():
            raise KeyError(bid)
        rows = conn.execute("SELECT * FROM removals WHERE batch_id=? AND status IN ('moved', 'conflict') ORDER BY id", (bid,)).fetchall()
        if not rows:
            raise ValueError("Nothing to restore in this batch")
        self._begin("restore", len(rows), sum(r["size"] or 0 for r in rows), bid)
        return rows

    def _run_restore(self, bid: int, rows: list) -> None:
        restored = conflicts = 0
        try:
            for r in rows:
                if self.cancel.is_set():
                    with self.lock:
                        self.state["cancelled"] = True
                    break
                src, dst = Path(r["quarantine_path"]), Path(r["root_path"]) / r["rel_path"]
                status, err = "restored", None
                if not src.exists():
                    status, err = "error", "missing from the quarantine folder"
                elif dst.exists():
                    status, err = "conflict", "a file now exists at the original location; left in quarantine"
                else:
                    try:
                        _move(src, dst, r["sha256"])
                    except Exception as e:  # noqa: BLE001
                        status, err = "conflict" if isinstance(e, FileExistsError) else "error", f"{type(e).__name__}: {e}"
                with db.tx() as c:
                    c.execute("UPDATE removals SET status=?, error=? WHERE id=?", (status, err, r["id"]))
                    if status == "restored":
                        c.execute("UPDATE files SET status='ok' WHERE id=? AND status='removed'", (r["file_id"],))
                if status == "restored":
                    restored += 1
                else:
                    conflicts += 1
                    self._err(str(dst), err or status)
                with self.lock:
                    self.state["done"] += 1
                    self.state["bytes_done"] += r["size"] or 0
                    self.state["moved"] = restored
                    self.state["conflicts"] = conflicts
        finally:
            left = db.get().execute("SELECT COUNT(*) FROM removals WHERE batch_id=? AND status IN ('moved','conflict')", (bid,)).fetchone()[0]
            with db.tx() as c:
                c.execute("UPDATE removal_batches SET state=?, restored_at=? WHERE id=?",
                          ("partially_restored" if left else "restored", time.time(), bid))
            b = db.get().execute("SELECT dest FROM removal_batches WHERE id=?", (bid,)).fetchone()
            if b and not left:
                _prune_empty_dirs(Path(b["dest"]))
            self._finish(bid, "", 0, 0, None)


def _write_manifest(bid: int, batch_dir: Path) -> None:
    rows = db.get().execute("SELECT root_path, rel_path, quarantine_path, size, status, error FROM removals WHERE batch_id=? ORDER BY id", (bid,))
    with open(batch_dir / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["original", "quarantine", "bytes", "status", "note"])
        for r in rows:
            w.writerow([str(Path(r["root_path"]) / r["rel_path"]), r["quarantine_path"], r["size"], r["status"], r["error"] or ""])


def _prune_empty_dirs(top: Path) -> None:
    """After a full restore, remove the now-empty batch folder tree (only empty folders and our manifest)."""
    if not top.is_dir():
        return
    for d, _dirs, files in os.walk(top, topdown=False):
        if files == ["manifest.csv"] and Path(d) == top:
            (top / "manifest.csv").unlink()
        try:
            os.rmdir(d)
        except OSError:
            pass


def batches(limit: int = 100) -> list[dict]:
    conn = db.get()
    out = []
    for b in conn.execute("SELECT * FROM removal_batches ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(b)
        d["counts"] = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM removals WHERE batch_id=? GROUP BY status", (b["id"],))}
        d["in_quarantine"] = d["counts"].get("moved", 0) + d["counts"].get("conflict", 0)
        out.append(d)
    return out


def batch_files(bid: int, limit: int = 2000) -> list[dict]:
    return [dict(r) for r in db.get().execute(
        "SELECT id, file_id, root_path, rel_path, quarantine_path, size, status, error FROM removals WHERE batch_id=? ORDER BY id LIMIT ?",
        (bid, limit))]


job = Job()
