"""SQLite schema and connection helpers."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS roots(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  added_at REAL NOT NULL
);

-- One row per file on disk. Metadata lives here; pixel-derived data lives in `images`.
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY,
  root_id INTEGER NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
  rel_path TEXT NOT NULL,
  name TEXT NOT NULL,
  ext TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  ctime REAL,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | ok | error | missing
  error TEXT,
  last_seen_scan INTEGER,
  sha256 TEXT,
  format TEXT, mode TEXT, width INTEGER, height INTEGER, has_alpha INTEGER, frames INTEGER,
  date_taken TEXT, date_source TEXT,
  camera_make TEXT, camera_model TEXT, lens TEXT, software TEXT,
  gps_lat REAL, gps_lon REAL, orientation INTEGER,
  has_exif INTEGER, exif_json TEXT, source_guess TEXT,
  UNIQUE(root_id, rel_path)
);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS ix_files_status ON files(status);
CREATE INDEX IF NOT EXISTS ix_files_ext ON files(ext);
CREATE INDEX IF NOT EXISTS ix_files_date ON files(date_taken);
CREATE INDEX IF NOT EXISTS ix_files_mtime ON files(mtime);
CREATE INDEX IF NOT EXISTS ix_files_camera ON files(camera_model);
CREATE INDEX IF NOT EXISTS ix_files_source ON files(source_guess);

-- One row per unique content (sha256). Byte-identical files share a row.
CREATE TABLE IF NOT EXISTS images(
  sha256 TEXT PRIMARY KEY,
  width INTEGER, height INTEGER,
  phash INTEGER, dhash INTEGER, whash INTEGER, colorhash TEXT,
  phash_variants TEXT,          -- pHash of rot90,rot180,rot270,mirror,bars-trimmed (comma separated)
  thumb TEXT,
  clip_row INTEGER
);
CREATE INDEX IF NOT EXISTS ix_images_phash ON images(phash);

-- Candidate pairs between unique images with every method's score.
CREATE TABLE IF NOT EXISTS pairs(
  a TEXT NOT NULL, b TEXT NOT NULL,
  phash_d INTEGER, dhash_d INTEGER, whash_d INTEGER, rot_d INTEGER, trim_d INTEGER,
  clip REAL, orb_inliers INTEGER, orb_ratio REAL,
  tier TEXT,                       -- visual tier (duplicate|near|similar) or NULL for text-only pairs
  text_sim REAL,                   -- OCR word-set Jaccard similarity (NULL = not compared)
  PRIMARY KEY(a, b)
);
CREATE INDEX IF NOT EXISTS ix_pairs_b ON pairs(b);

-- ORB results survive regrouping so they are never recomputed.
CREATE TABLE IF NOT EXISTS orb_cache(
  a TEXT NOT NULL, b TEXT NOT NULL, inliers INTEGER, ratio REAL,
  masked INTEGER DEFAULT 0,        -- 1 = OCR text regions were excluded
  PRIMARY KEY(a, b)
);

CREATE TABLE IF NOT EXISTS groups(
  id INTEGER PRIMARY KEY,
  tier TEXT NOT NULL,              -- exact | duplicate | near | similar
  size INTEGER NOT NULL,
  rep_file_id INTEGER,
  total_bytes INTEGER,
  reclaimable_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS ix_groups_tier ON groups(tier);

CREATE TABLE IF NOT EXISTS group_members(
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  file_id INTEGER NOT NULL,
  PRIMARY KEY(group_id, file_id)
);
CREATE INDEX IF NOT EXISTS ix_gm_file ON group_members(file_id);

-- User keep/discard marks. Keyed by path so they survive rescans.
CREATE TABLE IF NOT EXISTS decisions(
  root_id INTEGER NOT NULL,
  rel_path TEXT NOT NULL,
  decision TEXT NOT NULL,          -- keep | discard
  updated_at REAL NOT NULL,
  PRIMARY KEY(root_id, rel_path)
);

CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY,
  started REAL NOT NULL,
  finished REAL,
  state TEXT,
  summary TEXT
);

-- OCR result per unique image. Empty text = processed, no text found.
CREATE TABLE IF NOT EXISTS ocr(
  sha256 TEXT PRIMARY KEY,
  text TEXT,
  lines_json TEXT,                 -- [[text, confidence, [x0,y0,x1,y1] normalised]]
  norm TEXT,                       -- lowercase, accent-free, punctuation-free (for matching)
  n_words INTEGER,
  error TEXT,
  done_at REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
  sha256 UNINDEXED, text, tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    return conn


def init() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        conn = _connect()
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
        conn.close()
        _initialized = True


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by older versions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pairs)")}
    if "trim_d" not in cols:
        conn.execute("ALTER TABLE pairs ADD COLUMN trim_d INTEGER")
    if "text_sim" not in cols:
        conn.execute("ALTER TABLE pairs ADD COLUMN text_sim REAL")
    if "masked" not in {r[1] for r in conn.execute("PRAGMA table_info(orb_cache)")}:
        conn.execute("ALTER TABLE orb_cache ADD COLUMN masked INTEGER DEFAULT 0")


def get() -> sqlite3.Connection:
    """Thread-local connection."""
    init()
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_for_tests() -> None:
    global _initialized
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
    _initialized = False


def get_settings() -> dict[str, float]:
    rows = get().execute("SELECT key, value FROM settings").fetchall()
    out = dict(config.DEFAULT_SETTINGS)
    for r in rows:
        if r["key"] in out:
            out[r["key"]] = json.loads(r["value"])
    return out


def put_settings(values: dict[str, float]) -> dict[str, float]:
    with tx() as conn:
        for k, v in values.items():
            if k in config.DEFAULT_SETTINGS:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(float(v))),
                )
    return get_settings()
