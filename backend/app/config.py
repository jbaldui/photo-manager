"""Paths and tunable defaults. Everything the app writes lives under DATA_DIR."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PM_DATA_DIR", REPO_ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "library.db"
THUMB_DIR = DATA_DIR / "thumbs"
MODEL_DIR = Path(os.environ.get("PM_MODEL_DIR", DATA_DIR / "models")).resolve()
EXPORT_DIR = DATA_DIR / "exports"
GENERATED_DIR = DATA_DIR / "generated"
DEFAULT_QUARANTINE = Path.home() / "Photo Manager Quarantine"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.f16"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

WORKERS = int(os.environ.get("PM_WORKERS", max(1, (os.cpu_count() or 2))))
THUMB_SIZE = 320          # max side of stored WebP thumbnails (also used for CLIP + ORB)
WORK_SIZE = 512           # max side images are decoded to (thumbnail source)
HASH_SIZE = 256           # max side fed to perceptual hashes (they downscale to 8-32px anyway)
CLIP_MODEL = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"
CLIP_DIM = 512
CLIP_BATCH = 64

# Similarity thresholds. Editable at runtime from the Settings page (stored in the DB).
DEFAULT_SETTINGS: dict[str, float] = {
    # perceptual hash Hamming distances (64-bit hashes)
    "phash_candidate": 12,      # radius used to *find* candidate pairs
    "phash_duplicate": 6,       # pHash distance for the duplicate tier...
    "dhash_duplicate": 10,      # ...combined with dHash or wHash distance
    "rotated_duplicate": 4,     # pHash distance against a rotated/mirrored variant
    "trimmed_near": 6,          # pHash distance after stripping caption bars/borders -> near
    # CLIP cosine similarity
    "clip_candidate": 0.88,     # min cosine to keep a CLIP neighbour as candidate
    "clip_duplicate": 0.97,     # + ORB verified -> duplicate
    "clip_near": 0.90,          # + ORB verified -> near duplicate / edited
    "clip_similar": 0.92,       # unverified -> "similar" browse tier
    "clip_k": 16,               # neighbours per image
    # OCR same-text grouping
    "text_min_words": 3,        # ignore images with fewer recognised words (avoids "ok", logos)
    "text_jaccard": 0.8,        # word-set overlap to count as "same text" (1.0 = identical words)
    # ORB verification
    "orb_min_inliers": 25,
    "orb_min_ratio": 0.03,
}

# Quick "similarity strictness" scale, 1 (only near-identical images) .. 10 (very permissive).
# Every setting has an anchor at level 1, 5 and 10; level 5 equals DEFAULT_SETTINGS and the levels in
# between are interpolated, so each parameter moves in the right direction by its own amount.
SIMILARITY_ANCHORS: dict[str, tuple[float, float, float]] = {   # (level 1, level 5, level 10)
    "phash_candidate": (6, 12, 20),
    "phash_duplicate": (2, 6, 12),
    "dhash_duplicate": (4, 10, 18),
    "rotated_duplicate": (1, 4, 8),
    "trimmed_near": (2, 6, 12),
    "clip_candidate": (0.94, 0.88, 0.80),
    "clip_duplicate": (0.99, 0.97, 0.94),
    "clip_near": (0.96, 0.90, 0.84),
    "clip_similar": (0.97, 0.92, 0.86),
    "clip_k": (8, 16, 32),
    "text_min_words": (5, 3, 2),
    "text_jaccard": (0.95, 0.8, 0.6),
    "orb_min_inliers": (40, 25, 15),
    "orb_min_ratio": (0.06, 0.03, 0.015),
}


def similarity_preset(level: int) -> dict[str, float]:
    level = max(1, min(10, int(level)))
    out: dict[str, float] = {}
    for key, (lo, mid, hi) in SIMILARITY_ANCHORS.items():
        a, b, t = (lo, mid, (level - 1) / 4) if level <= 5 else (mid, hi, (level - 5) / 5)
        v = a + (b - a) * t
        out[key] = round(v) if float(DEFAULT_SETTINGS[key]).is_integer() else round(v, 4)
    return out


for d in (DATA_DIR, THUMB_DIR, MODEL_DIR, EXPORT_DIR, GENERATED_DIR):
    d.mkdir(parents=True, exist_ok=True)
