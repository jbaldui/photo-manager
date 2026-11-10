"""Stage 1 (walk) and stage 2 (per-file analysis) of the pipeline."""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from . import config, db, decode, hashing

NON_IMAGE_EXTS = {
    "mp4", "mov", "avi", "mkv", "wmv", "m4v", "3gp", "webm", "mp3", "wav", "m4a", "aac", "ogg", "opus",
    "txt", "json", "xml", "html", "htm", "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "zip", "rar",
    "7z", "gz", "tar", "exe", "dll", "db", "ini", "lnk", "xmp", "aae", "thm", "ds_store", "py", "js", "css",
}


def _walk(root: str):
    data_dir = str(config.DATA_DIR)
    stack = [root]
    while stack:
        d = stack.pop()
        if d == data_dir or d.startswith(data_dir + os.sep):
            continue
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith("."):
                            stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        yield e
                except OSError:
                    continue


def _is_image(entry: os.DirEntry) -> tuple[bool, str]:
    name = entry.name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in decode.IMAGE_EXTS:
        return True, ext
    if ext in NON_IMAGE_EXTS or name.startswith("."):
        return False, ext
    sniffed = decode.sniff(entry.path)
    return (sniffed is not None), (sniffed or ext)


def scan_roots(scan_id: int, ctx) -> dict:
    """Walk every root, insert new files, mark changed ones pending and vanished ones missing."""
    conn = db.get()
    roots = conn.execute("SELECT id, path FROM roots").fetchall()
    stats = {"seen": 0, "new": 0, "changed": 0, "missing": 0}
    for r in roots:
        root_id, root_path = r["id"], r["path"]
        if not os.path.isdir(root_path):
            ctx.message(f"Folder not found: {root_path}")
            continue
        existing = {
            row["rel_path"]: (row["id"], row["size"], row["mtime"], row["status"])
            for row in conn.execute("SELECT id, rel_path, size, mtime, status FROM files WHERE root_id=?", (root_id,))
        }
        new_rows, changed_rows, seen_ids = [], [], []

        def flush():
            with db.tx() as c:
                if new_rows:
                    c.executemany(
                        "INSERT INTO files(root_id, rel_path, name, ext, size, mtime, ctime, status, last_seen_scan)"
                        " VALUES(?,?,?,?,?,?,?,'pending',?)",
                        new_rows,
                    )
                if changed_rows:
                    c.executemany(
                        "UPDATE files SET size=?, mtime=?, ctime=?, status='pending', error=NULL, last_seen_scan=?"
                        " WHERE id=?",
                        changed_rows,
                    )
                if seen_ids:
                    c.executemany("UPDATE files SET last_seen_scan=? WHERE id=?", [(scan_id, i) for i in seen_ids])
            new_rows.clear(), changed_rows.clear(), seen_ids.clear()

        for entry in _walk(root_path):
            if ctx.cancelled():
                break
            ok, ext = _is_image(entry)
            if not ok:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            rel = os.path.relpath(entry.path, root_path)
            stats["seen"] += 1
            prev = existing.get(rel)
            if prev is None:
                new_rows.append((root_id, rel, entry.name, ext, st.st_size, st.st_mtime, st.st_ctime, scan_id))
                stats["new"] += 1
            elif prev[1] != st.st_size or abs(prev[2] - st.st_mtime) > 1e-6 or prev[3] == "missing":
                changed_rows.append((st.st_size, st.st_mtime, st.st_ctime, scan_id, prev[0]))
                stats["changed"] += 1
            else:
                seen_ids.append(prev[0])
            if stats["seen"] % 2000 == 0:
                flush()
                ctx.update(stats["seen"], None, f"Found {stats['seen']:,} images")
        flush()
        if not ctx.cancelled():
            with db.tx() as c:
                cur = c.execute(
                    "UPDATE files SET status='missing' WHERE root_id=? AND (last_seen_scan IS NULL OR last_seen_scan<>?)"
                    " AND status<>'missing'",
                    (root_id, scan_id),
                )
                stats["missing"] += cur.rowcount
    ctx.update(stats["seen"], stats["seen"], f"Found {stats['seen']:,} images ({stats['new']:,} new, {stats['changed']:,} changed)")
    return stats


_FILE_COLS = [
    "sha256", "format", "mode", "width", "height", "has_alpha", "frames", "date_taken", "date_source",
    "camera_make", "camera_model", "lens", "software", "gps_lat", "gps_lon", "orientation", "has_exif",
    "exif_json", "source_guess",
]
_IMG_COLS = ["sha256", "width", "height", "phash", "dhash", "whash", "colorhash", "phash_variants", "thumb"]


def _write_results(results: list[tuple[int, dict]]) -> None:
    with db.tx() as c:
        for fid, res in results:
            if "error" in res:
                c.execute("UPDATE files SET status='error', error=? WHERE id=?", (res["error"], fid))
                continue
            sets = ", ".join(f"{k}=?" for k in _FILE_COLS)
            c.execute(
                f"UPDATE files SET {sets}, status='ok', error=NULL WHERE id=?",
                [res.get(k) for k in _FILE_COLS] + [fid],
            )
            img = res.get("image")
            if img:
                c.execute(
                    f"INSERT INTO images({', '.join(_IMG_COLS)}) VALUES({', '.join('?' * len(_IMG_COLS))})"
                    " ON CONFLICT(sha256) DO UPDATE SET " + ", ".join(f"{k}=excluded.{k}" for k in _IMG_COLS[1:]),
                    [img.get(k) for k in _IMG_COLS],
                )


def analyze_pending(ctx, batch_size: int = 24) -> dict:
    conn = db.get()
    rows = conn.execute(
        "SELECT f.id, r.path AS root, f.rel_path FROM files f JOIN roots r ON r.id=f.root_id"
        " WHERE f.status='pending' ORDER BY f.root_id, f.rel_path"
    ).fetchall()
    total = len(rows)
    ctx.update(0, total, f"Analysing {total:,} files")
    if not total:
        return {"analysed": 0, "errors": 0}
    jobs = [[(r["id"], str(Path(r["root"]) / r["rel_path"])) for r in rows[i : i + batch_size]] for i in range(0, total, batch_size)]
    done = errors = 0
    pending_results: list[tuple[int, dict]] = []
    last_flush = time.time()
    with ProcessPoolExecutor(
        max_workers=config.WORKERS,
        mp_context=mp.get_context("spawn"),
        initializer=hashing.init_worker,
        initargs=(str(config.DB_PATH),),
    ) as pool:
        it = iter(jobs)
        inflight = set()
        for _ in range(config.WORKERS * 3):
            j = next(it, None)
            if j is None:
                break
            inflight.add(pool.submit(hashing.analyze_batch, j))
        while inflight:
            finished, inflight = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in finished:
                res = fut.result()
                pending_results.extend(res)
                done += len(res)
                errors += sum(1 for _, r in res if "error" in r)
                if not ctx.cancelled():
                    j = next(it, None)
                    if j is not None:
                        inflight.add(pool.submit(hashing.analyze_batch, j))
            if len(pending_results) >= 500 or time.time() - last_flush > 2:
                _write_results(pending_results)
                pending_results.clear()
                last_flush = time.time()
            ctx.update(done, total, f"Analysed {done:,} / {total:,} files ({errors:,} unreadable)")
    _write_results(pending_results)
    return {"analysed": done, "errors": errors}
