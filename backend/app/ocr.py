"""Stage: OCR (text in images) with RapidOCR (PaddleOCR models on onnxruntime, fully local).

- Detector: PP-OCRv4 mobile (any script), run at <= OCR_DET_SIZE px for speed.
- Recognizer: PP-OCRv5 Latin (English, Portuguese, Spanish, French, German, ... incl. accents).
- Images most likely to contain text (by CLIP zero-shot) are processed first, so screenshots,
  memes and documents become searchable long before the full pass completes.
- Results: `ocr` table (per unique image) + `ocr_fts` (SQLite FTS5, accent-insensitive search).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import re
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np

from . import config, db, decode

OCR_DIR = config.MODEL_DIR / "ocr"
OCR_DET_SIZE = 640       # detector input (long side); 640 keeps small screenshot text readable
OCR_READ_SIZE = 1280     # image decoded at this size; recognizer crops come from it
MIN_CONF = 0.75          # drop low-confidence lines (mostly false detections on textures)

_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

        OCR_DIR.mkdir(parents=True, exist_ok=True)
        _ENGINE = RapidOCR(
            params={
                "Global.use_cls": False,
                "Global.log_level": "error",
                "Global.model_root_dir": str(OCR_DIR),
                "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                "Det.ocr_version": OCRVersion.PPOCRV4,
                "Det.model_type": ModelType.MOBILE,
                "Det.lang_type": LangDet.CH,
                "Det.limit_type": "max",
                "Det.limit_side_len": OCR_DET_SIZE,
                "Rec.lang_type": LangRec.LATIN,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
            }
        )
    return _ENGINE


def init_worker() -> None:
    _engine()


_HAS_ALNUM = re.compile(r"[^\W_]", re.UNICODE)


def ocr_file(path: str) -> dict:
    """Run OCR on one image file (read-only). Returns {'lines': [[text, conf, [x0,y0,x1,y1]]...]}."""
    try:
        img = decode.to_working_rgb(decode.open_image(path), OCR_READ_SIZE)
        arr = np.ascontiguousarray(np.asarray(img)[:, :, ::-1])
        res = _engine()(arr)
        lines = []
        if res.txts:
            w, h = img.size
            for txt, score, box in zip(res.txts, res.scores, res.boxes):
                txt = txt.strip()
                if float(score) < MIN_CONF or len(txt) < 2 or not _HAS_ALNUM.search(txt):
                    continue
                b = np.asarray(box)
                x0, y0 = (float(v) for v in b.min(axis=0))
                x1, y1 = (float(v) for v in b.max(axis=0))
                lines.append([txt, round(float(score), 3), [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)]])
            # reading order: top-to-bottom, then left-to-right
            lines.sort(key=lambda l: (round(l[2][1] * 40), l[2][0]))
        return {"lines": lines}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def ocr_batch(items: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    return [(sha, ocr_file(p)) for sha, p in items]


# ----------------------------------------------------------------- text normalisation
def normalize(text: str) -> str:
    """Lowercase, strip accents, keep letters/digits only, collapse spaces."""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[\W_]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def tokens(norm: str) -> set[str]:
    return {w for w in norm.split() if len(w) >= 2}


def fts_query(q: str) -> str | None:
    """User text -> safe FTS5 query. "quoted phrase" kept as phrase; otherwise AND of words (last is prefix)."""
    q = q.strip()
    if not q:
        return None
    phrases = re.findall(r'"([^"]+)"', q)
    rest = re.sub(r'"[^"]+"', " ", q)
    parts = [f'"{normalize(p)}"' for p in phrases if normalize(p)]
    words = normalize(rest).split()
    for i, w in enumerate(words):
        parts.append(f'"{w}"*' if i == len(words) - 1 else f'"{w}"')
    return " AND ".join(parts) or None


# ----------------------------------------------------------------- pipeline stage
TEXT_PROMPTS = ["a screenshot", "a meme with a caption", "a photo of a document", "text", "a sign with writing", "a chat conversation"]
PHOTO_PROMPTS = ["a photo", "a landscape photo", "a photo of people", "a close-up photo"]


def _priority(rows) -> list:
    """Sort pending images by CLIP text-likelihood (most likely text first)."""
    try:
        from .embed import clip, store

        emb = store.load()
        if not len(emb):
            return list(rows)
        t = np.stack([clip.encode_text(p) for p in TEXT_PROMPTS])
        ph = np.stack([clip.encode_text(p) for p in PHOTO_PROMPTS])

        def score(r):
            if r["clip_row"] is None:
                return -1.0
            v = np.asarray(emb[r["clip_row"]], dtype=np.float32)
            return float((t @ v).max() - (ph @ v).max())

        return sorted(rows, key=score, reverse=True)
    except Exception:
        return list(rows)


def _write(results: list[tuple[str, dict]]) -> None:
    now = time.time()
    with db.tx() as c:
        for sha, res in results:
            if "error" in res:
                c.execute(
                    "INSERT OR REPLACE INTO ocr(sha256, text, lines_json, norm, n_words, error, done_at) VALUES(?,?,?,?,?,?,?)",
                    (sha, "", "[]", "", 0, res["error"], now),
                )
                continue
            text = "\n".join(l[0] for l in res["lines"])
            norm = normalize(text)
            c.execute(
                "INSERT OR REPLACE INTO ocr(sha256, text, lines_json, norm, n_words, error, done_at) VALUES(?,?,?,?,?,NULL,?)",
                (sha, text, json.dumps(res["lines"], ensure_ascii=False), norm, len(norm.split()) if norm else 0, now),
            )
            c.execute("DELETE FROM ocr_fts WHERE sha256=?", (sha,))
            if norm:
                c.execute("INSERT INTO ocr_fts(sha256, text) VALUES(?, ?)", (sha, text))


def ocr_pending(ctx) -> dict:
    conn = db.get()
    rows = conn.execute(
        """SELECT i.sha256, i.clip_row, MIN(r.path || '/' || f.rel_path) AS path
           FROM images i JOIN files f ON f.sha256=i.sha256 AND f.status='ok' JOIN roots r ON r.id=f.root_id
           WHERE NOT EXISTS (SELECT 1 FROM ocr o WHERE o.sha256=i.sha256)
           GROUP BY i.sha256"""
    ).fetchall()
    total = len(rows)
    ctx.update(0, total, f"OCR: {total:,} images to read")
    if not total:
        return {"ocr": 0, "with_text": 0}
    ctx.update(0, total, "Loading OCR models (first run downloads ~15 MB)")
    _engine()  # download models once, in this process, before the workers start
    ordered = _priority(rows)
    jobs = [[(r["sha256"], r["path"]) for r in ordered[i : i + 8]] for i in range(0, total, 8)]
    done = with_text = 0
    buf: list[tuple[str, dict]] = []
    last = time.time()
    with ProcessPoolExecutor(max_workers=config.WORKERS, mp_context=mp.get_context("spawn"), initializer=init_worker) as pool:
        it = iter(jobs)
        inflight = {pool.submit(ocr_batch, j) for j in (next(it, None) for _ in range(config.WORKERS * 2)) if j}
        while inflight:
            fin, inflight = wait(inflight, return_when=FIRST_COMPLETED)
            for f in fin:
                res = f.result()
                buf.extend(res)
                done += len(res)
                with_text += sum(1 for _, r in res if r.get("lines"))
                if not ctx.cancelled():
                    j = next(it, None)
                    if j:
                        inflight.add(pool.submit(ocr_batch, j))
            if len(buf) >= 64 or time.time() - last > 3:
                _write(buf)
                buf.clear()
                last = time.time()
            ctx.update(done, total, f"OCR read {done:,} / {total:,} images · {with_text:,} with text")
    _write(buf)
    return {"ocr": done, "with_text": with_text}


# ----------------------------------------------------------------- same-text pairs
def text_pairs(min_words: int, min_jaccard: float, max_df: int = 200) -> list[tuple[str, str, float]]:
    """Pairs of images whose recognised text is (nearly) the same: Jaccard over word sets.

    Candidates come from an inverted index over *rare* words (very common words/watermarks that
    appear in more than `max_df` images are not used to find candidates, only to score them).
    """
    rows = db.get().execute(
        "SELECT o.sha256, o.norm FROM ocr o WHERE o.n_words >= ? AND o.sha256 IN (SELECT sha256 FROM files WHERE status='ok')",
        (int(min_words),),
    ).fetchall()
    shas = [r["sha256"] for r in rows]
    sets = [tokens(r["norm"]) for r in rows]
    index: dict[str, list[int]] = {}
    for i, s in enumerate(sets):
        for w in s:
            index.setdefault(w, []).append(i)
    rare = {w: ids for w, ids in index.items() if len(ids) <= max_df}
    out = []
    for i, s in enumerate(sets):
        if len(s) < min_words:
            continue
        counts: dict[int, int] = {}
        for w in s:
            for j in rare.get(w, ()):
                if j > i:
                    counts[j] = counts.get(j, 0) + 1
        for j, shared_rare in counts.items():
            sj = sets[j]
            # quick bound: Jaccard <= min/max size ratio
            if min(len(s), len(sj)) / max(len(s), len(sj)) < min_jaccard:
                continue
            inter = len(s & sj)
            jac = inter / len(s | sj)
            if jac >= min_jaccard:
                a, b = sorted((shas[i], shas[j]))
                out.append((a, b, round(jac, 4)))
    return out


def get(sha: str) -> dict | None:
    r = db.get().execute("SELECT text, lines_json, n_words, error FROM ocr WHERE sha256=?", (sha,)).fetchone()
    if not r:
        return None
    return {"text": r["text"], "lines": json.loads(r["lines_json"] or "[]"), "n_words": r["n_words"], "error": r["error"]}
