"""Library maintenance: removing a source folder and everything derived from it.

Only the app's own data (database rows, thumbnails) is deleted - files on disk in the
removed folder are never touched.
"""
from __future__ import annotations

from . import config, db, grouping


class _QuietCtx:
    def cancelled(self) -> bool:
        return False

    def message(self, msg: str) -> None:
        pass

    def update(self, processed, total, msg=None) -> None:
        pass


def _purge_orphans() -> tuple[int, int]:
    """Delete derived data for content that no longer exists in any folder. Returns (images, thumbnails)."""
    with db.tx() as c:
        c.execute("DELETE FROM decisions WHERE root_id NOT IN (SELECT id FROM roots)")
        c.execute("DELETE FROM group_members WHERE file_id NOT IN (SELECT id FROM files)")
        c.execute("CREATE TEMP TABLE IF NOT EXISTS orphan_sha(sha256 TEXT PRIMARY KEY)")
        c.execute("DELETE FROM orphan_sha")
        c.execute("INSERT INTO orphan_sha SELECT sha256 FROM images WHERE sha256 NOT IN (SELECT sha256 FROM files WHERE sha256 IS NOT NULL)")
        thumbs = [r[0] for r in c.execute("SELECT thumb FROM images WHERE sha256 IN (SELECT sha256 FROM orphan_sha) AND thumb IS NOT NULL")]
        n_images = c.execute("SELECT COUNT(*) FROM orphan_sha").fetchone()[0]
        for table, cond in (
            ("pairs", "a IN (SELECT sha256 FROM orphan_sha) OR b IN (SELECT sha256 FROM orphan_sha)"),
            ("orb_cache", "a IN (SELECT sha256 FROM orphan_sha) OR b IN (SELECT sha256 FROM orphan_sha)"),
            ("ocr_fts", "sha256 IN (SELECT sha256 FROM orphan_sha)"),
            ("ocr", "sha256 IN (SELECT sha256 FROM orphan_sha)"),
            ("images", "sha256 IN (SELECT sha256 FROM orphan_sha)"),
        ):
            c.execute(f"DELETE FROM {table} WHERE {cond}")
        c.execute("DELETE FROM orphan_sha")
    # thumbnails are keyed by content hash and only ever written by the app
    removed = 0
    base = config.THUMB_DIR.resolve()
    for rel in thumbs:
        p = (config.THUMB_DIR / rel).resolve()
        if base in p.parents and p.is_file():
            p.unlink()
            removed += 1
    return n_images, removed


def needs_repair() -> bool:
    """True when groups or analysis data refer to files that no longer exist (e.g. a folder
    removed by an older version of the app, or files deleted from the database)."""
    conn = db.get()
    checks = (
        # a group whose recorded size no longer matches its members, or whose best copy is gone
        """SELECT 1 FROM groups g WHERE g.size <> (SELECT COUNT(*) FROM group_members m JOIN files f ON f.id=m.file_id WHERE m.group_id=g.id)
           OR g.rep_file_id NOT IN (SELECT id FROM files) LIMIT 1""",
        "SELECT 1 FROM images WHERE sha256 NOT IN (SELECT sha256 FROM files WHERE sha256 IS NOT NULL) LIMIT 1",
        "SELECT 1 FROM group_members WHERE file_id NOT IN (SELECT id FROM files) LIMIT 1",
        "SELECT 1 FROM decisions WHERE root_id NOT IN (SELECT id FROM roots) LIMIT 1",
    )
    return any(conn.execute(q).fetchone() for q in checks)


def repair() -> dict | None:
    """Bring the library back to a consistent state. Runs at server start-up; no-op when consistent."""
    if not needs_repair():
        return None
    images, thumbs = _purge_orphans()
    return {"images": images, "thumbnails": thumbs, "groups": grouping.build_groups(_QuietCtx())}


def remove_root(root_id: int) -> dict:
    """Forget a source folder.

    - its files, their group memberships and keep/discard decisions
    - content (by SHA-256) that no longer exists in any remaining folder: image fingerprints,
      thumbnails, OCR text, candidate pairs and cached ORB results
    - groups are rebuilt, so photos elsewhere are no longer listed as duplicates of removed files

    Content that still exists in another folder is kept, so nothing needs re-analysing.
    CLIP vectors of orphaned images stay in the append-only embeddings file but are no longer referenced.
    """
    conn = db.get()
    if not conn.execute("SELECT 1 FROM roots WHERE id=?", (root_id,)).fetchone():
        raise KeyError(root_id)
    n_files = conn.execute("SELECT COUNT(*) FROM files WHERE root_id=?", (root_id,)).fetchone()[0]
    with db.tx() as c:
        c.execute("DELETE FROM group_members WHERE file_id IN (SELECT id FROM files WHERE root_id=?)", (root_id,))
        c.execute("DELETE FROM decisions WHERE root_id=?", (root_id,))
        c.execute("DELETE FROM files WHERE root_id=?", (root_id,))
        c.execute("DELETE FROM roots WHERE id=?", (root_id,))
    n_images, removed_thumbs = _purge_orphans()
    counts = grouping.build_groups(_QuietCtx())
    return {"files": n_files, "images": n_images, "thumbnails": removed_thumbs, "groups": counts}
