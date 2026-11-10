"""Stage 3: CLIP embeddings (semantic similarity + text search), stored as an append-only float16 matrix."""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import config, db

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HOME", str(config.MODEL_DIR))


class _Clip:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.model = None
        self.preprocess = None
        self.tokenizer = None

    def load(self):
        with self._lock:
            if self.model is None:
                import open_clip
                import torch

                torch.set_num_threads(config.WORKERS)
                model, _, preprocess = open_clip.create_model_and_transforms(
                    config.CLIP_MODEL, pretrained=config.CLIP_PRETRAINED, cache_dir=str(config.MODEL_DIR)
                )
                model.eval()
                self.model, self.preprocess = model, preprocess
                self.tokenizer = open_clip.get_tokenizer(config.CLIP_MODEL)
        return self

    def encode_images(self, imgs: list[Image.Image]) -> np.ndarray:
        import torch

        self.load()
        x = torch.stack([self.preprocess(im) for im in imgs])
        with torch.inference_mode():
            f = self.model.encode_image(x)
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy().astype(np.float32)

    def encode_text(self, text: str) -> np.ndarray:
        import torch

        self.load()
        with torch.inference_mode():
            f = self.model.encode_text(self.tokenizer([text]))
            f = f / f.norm(dim=-1, keepdim=True)
        return f.cpu().numpy().astype(np.float32)[0]


clip = _Clip()


class EmbeddingStore:
    """Rows of float16[CLIP_DIM] appended to a flat file; `images.clip_row` indexes into it."""

    path = config.EMBEDDINGS_PATH
    row_bytes = config.CLIP_DIM * 2

    def count(self) -> int:
        return self.path.stat().st_size // self.row_bytes if self.path.exists() else 0

    def append(self, vecs: np.ndarray) -> int:
        start = self.count()
        with open(self.path, "ab") as f:
            f.truncate(start * self.row_bytes)   # drop any partial row from an interrupted write
            f.write(vecs.astype(np.float16).tobytes())
            f.flush()
            os.fsync(f.fileno())
        return start

    def load(self) -> np.ndarray:
        n = self.count()
        if n == 0:
            return np.zeros((0, config.CLIP_DIM), dtype=np.float16)
        return np.memmap(self.path, dtype=np.float16, mode="r", shape=(n, config.CLIP_DIM))


store = EmbeddingStore()


def _load_thumb(rel: str) -> Image.Image | None:
    try:
        with Image.open(config.THUMB_DIR / rel) as im:
            return im.convert("RGB")
    except Exception:
        return None


def embed_pending(ctx) -> dict:
    conn = db.get()
    rows = conn.execute("SELECT sha256, thumb FROM images WHERE clip_row IS NULL AND thumb IS NOT NULL").fetchall()
    total = len(rows)
    ctx.update(0, total, f"Embedding {total:,} images with CLIP")
    if not total:
        return {"embedded": 0}
    ctx.update(0, total, "Loading CLIP model (first run downloads ~600 MB)")
    clip.load()
    done = 0
    bs = config.CLIP_BATCH
    with ThreadPoolExecutor(max_workers=2) as io_pool:
        batches = [rows[i : i + bs] for i in range(0, total, bs)]
        nxt = io_pool.submit(lambda b: [_load_thumb(r["thumb"]) for r in b], batches[0])
        for bi, batch in enumerate(batches):
            if ctx.cancelled():
                break
            imgs = nxt.result()
            if bi + 1 < len(batches):
                nxt = io_pool.submit(lambda b: [_load_thumb(r["thumb"]) for r in b], batches[bi + 1])
            keep = [(r["sha256"], im) for r, im in zip(batch, imgs) if im is not None]
            if keep:
                vecs = clip.encode_images([im for _, im in keep])
                start = store.append(vecs)
                with db.tx() as c:
                    c.executemany(
                        "UPDATE images SET clip_row=? WHERE sha256=?",
                        [(start + i, sha) for i, (sha, _) in enumerate(keep)],
                    )
            done += len(batch)
            ctx.update(done, total, f"CLIP embedded {done:,} / {total:,}")
    return {"embedded": done}


def search_text(query: str, limit: int = 100) -> list[tuple[str, float]]:
    emb = store.load()
    if len(emb) == 0:
        return []
    q = clip.encode_text(query)
    rows = db.get().execute("SELECT sha256, clip_row FROM images WHERE clip_row IS NOT NULL").fetchall()
    idx = np.array([r["clip_row"] for r in rows])
    scores = (np.asarray(emb[idx], dtype=np.float32) @ q)
    top = np.argsort(-scores)[:limit]
    return [(rows[i]["sha256"], float(scores[i])) for i in top]


def similar_to(sha: str, limit: int = 30) -> list[tuple[str, float]]:
    conn = db.get()
    r = conn.execute("SELECT clip_row FROM images WHERE sha256=?", (sha,)).fetchone()
    if not r or r["clip_row"] is None:
        return []
    emb = store.load()
    q = np.asarray(emb[r["clip_row"]], dtype=np.float32)
    rows = conn.execute("SELECT sha256, clip_row FROM images WHERE clip_row IS NOT NULL").fetchall()
    idx = np.array([x["clip_row"] for x in rows])
    scores = np.asarray(emb[idx], dtype=np.float32) @ q
    top = np.argsort(-scores)[: limit + 1]
    return [(rows[i]["sha256"], float(scores[i])) for i in top if rows[i]["sha256"] != sha][:limit]
