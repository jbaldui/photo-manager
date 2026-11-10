"""Stage 4: find candidate pairs (perceptual hashes + CLIP), score them with every method,
verify with ORB where needed and assign a tier to each pair."""
from __future__ import annotations

import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor

import faiss
import numpy as np

from . import config, db, verify
from .hashing import to_unsigned64

faiss.omp_set_num_threads(config.WORKERS)


def _u64(values) -> np.ndarray:
    return np.array([to_unsigned64(int(v)) for v in values], dtype=np.uint64)


def _bytes(arr: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(arr).view(np.uint8).reshape(-1, 8)


def _ham(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.bitwise_count(np.bitwise_xor(x, y)).astype(np.int16)


def _text_boxes(conn) -> dict[str, tuple]:
    """OCR text boxes per image (normalised), used to mask text out of ORB matching."""
    import json

    out = {}
    for r in conn.execute("SELECT sha256, lines_json FROM ocr WHERE n_words > 0"):
        b = tuple(tuple(l[2]) for l in json.loads(r["lines_json"] or "[]"))
        if b:
            out[r["sha256"]] = b
    return out


def _range_pairs(index, queries: np.ndarray, radius: int, chunk: int = 20000) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for s in range(0, len(queries), chunk):
        lims, _, I = index.range_search(_bytes(queries[s : s + chunk]), radius + 1)  # faiss radius is strict
        for qi in range(len(lims) - 1):
            q = s + qi
            for j in I[lims[qi] : lims[qi + 1]]:
                j = int(j)
                if j != q:
                    out.add((q, j) if q < j else (j, q))
    return out


def _build_visual_pairs(ctx, st) -> dict:
    conn = db.get()
    rows = conn.execute(
        "SELECT sha256, phash, dhash, whash, phash_variants, clip_row, thumb FROM images"
        " WHERE phash IS NOT NULL AND sha256 IN (SELECT sha256 FROM files WHERE status='ok')"
        " ORDER BY sha256"
    ).fetchall()
    n = len(rows)
    ctx.update(0, 4, f"Finding candidate pairs among {n:,} unique images")
    if n < 2:
        with db.tx() as c:
            c.execute("DELETE FROM pairs")
        return {"pairs": 0}
    shas = [r["sha256"] for r in rows]
    thumbs = [r["thumb"] for r in rows]
    ph = _u64(r["phash"] for r in rows)
    dh = _u64(r["dhash"] for r in rows)
    wh = _u64(r["whash"] for r in rows)
    var = np.stack([_u64(r["phash_variants"].split(",")) for r in rows])  # (n, 5): rot90/180/270, mirror, trimmed

    # --- Method 2: perceptual hash radius search (faiss binary index) -------------------------
    index = faiss.IndexBinaryFlat(64)
    index.add(_bytes(ph))
    cand = _range_pairs(index, ph, int(st["phash_candidate"]))
    for k in range(4):                     # rotated / mirrored copies
        cand |= _range_pairs(index, var[:, k].copy(), int(st["rotated_duplicate"]))
    # caption bars / borders added (memes): compare trimmed content with every other image
    cand |= _range_pairs(index, var[:, 4].copy(), int(st["trimmed_near"]))
    tindex = faiss.IndexBinaryFlat(64)
    tindex.add(_bytes(var[:, 4].copy()))
    cand |= _range_pairs(tindex, var[:, 4].copy(), int(st["trimmed_near"]))
    ctx.update(1, 4, f"{len(cand):,} candidate pairs from perceptual hashes")

    # --- Method 3: CLIP nearest neighbours --------------------------------------------------
    clip_rows = np.array([r["clip_row"] if r["clip_row"] is not None else -1 for r in rows])
    has_clip = clip_rows >= 0
    emb_all = None
    from .embed import store

    stored = store.load()
    if has_clip.any() and len(stored):
        emb_all = np.zeros((n, config.CLIP_DIM), dtype=np.float32)
        emb_all[has_clip] = np.asarray(stored[clip_rows[has_clip]], dtype=np.float32)
        norms = np.linalg.norm(emb_all, axis=1, keepdims=True)
        emb_all = np.where(norms > 0, emb_all / np.maximum(norms, 1e-6), 0).astype(np.float32)
        sub = np.nonzero(has_clip)[0]
        X = np.ascontiguousarray(emb_all[sub])
        if len(sub) <= 60000:
            cidx = faiss.IndexFlatIP(config.CLIP_DIM)
        else:
            cidx = faiss.IndexHNSWFlat(config.CLIP_DIM, 32, faiss.METRIC_INNER_PRODUCT)
            cidx.hnsw.efConstruction = 40
            cidx.hnsw.efSearch = 96
        cidx.add(X)
        k = min(int(st["clip_k"]) + 1, len(sub))
        before = len(cand)
        for s in range(0, len(sub), 20000):
            D, I = cidx.search(X[s : s + 20000], k)
            for qi in range(D.shape[0]):
                q = int(sub[s + qi])
                for dist, j in zip(D[qi], I[qi]):
                    if j < 0 or dist < st["clip_candidate"]:
                        continue
                    j = int(sub[j])
                    if j != q:
                        cand.add((q, j) if q < j else (j, q))
        ctx.update(2, 4, f"{len(cand) - before:,} extra candidate pairs from CLIP")

    if not cand:
        with db.tx() as c:
            c.execute("DELETE FROM pairs")
        return {"pairs": 0}

    pairs = np.array(sorted(cand), dtype=np.int64)
    A, B = pairs[:, 0], pairs[:, 1]
    phd, dhd, whd = _ham(ph[A], ph[B]), _ham(dh[A], dh[B]), _ham(wh[A], wh[B])
    rot = np.min(np.stack([_ham(var[A, k], ph[B]) for k in range(4)] + [_ham(var[B, k], ph[A]) for k in range(4)]), axis=0)
    trim = np.min(np.stack([_ham(var[A, 4], ph[B]), _ham(var[B, 4], ph[A]), _ham(var[A, 4], var[B, 4])]), axis=0)
    if emb_all is not None:
        clip = np.einsum("ij,ij->i", emb_all[A], emb_all[B])
        clip = np.where(has_clip[A] & has_clip[B], clip, np.nan)
    else:
        clip = np.full(len(A), np.nan)

    hash_dup = ((phd <= st["phash_duplicate"]) & ((dhd <= st["dhash_duplicate"]) | (whd <= st["dhash_duplicate"]))) | (
        rot <= st["rotated_duplicate"]
    )
    clip0 = np.nan_to_num(clip, nan=0.0)
    # --- Method 4: ORB geometric verification, only where it decides the outcome ------------
    trim_near = ~hash_dup & (trim <= st["trimmed_near"])
    need = ~hash_dup & ((clip0 >= st["clip_near"]) | (phd <= st["phash_candidate"]) | trim_near)
    need_idx = np.nonzero(need)[0]
    orb_in = np.zeros(len(A), dtype=np.int32)
    orb_ratio = np.zeros(len(A), dtype=np.float32)
    cache = {}
    if len(need_idx):
        keys = [(shas[A[i]], shas[B[i]]) for i in need_idx]
        boxes = _text_boxes(conn)
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS need_orb(a TEXT, b TEXT)")
        conn.execute("DELETE FROM need_orb")
        conn.executemany("INSERT INTO need_orb VALUES(?, ?)", keys)
        for r in conn.execute("SELECT c.a, c.b, c.inliers, c.ratio, c.masked FROM need_orb n JOIN orb_cache c ON c.a=n.a AND c.b=n.b"):
            # a result computed before OCR knew about text in either image is stale
            if bool(r["masked"]) == bool(boxes.get(r["a"]) or boxes.get(r["b"])):
                cache[(r["a"], r["b"])] = (r["inliers"], r["ratio"])
        conn.execute("DELETE FROM need_orb")
        conn.commit()
        todo = [
            (shas[A[i]], shas[B[i]], thumbs[A[i]], thumbs[B[i]], boxes.get(shas[A[i]], ()), boxes.get(shas[B[i]], ()))
            for i in need_idx
            if (shas[A[i]], shas[B[i]]) not in cache
        ]
        ctx.update(3, 4, f"ORB-verifying {len(todo):,} pairs ({len(need_idx) - len(todo):,} cached)")
        if todo:
            batches = [todo[i : i + 200] for i in range(0, len(todo), 200)]
            done = 0
            with ProcessPoolExecutor(max_workers=config.WORKERS, mp_context=mp.get_context("spawn")) as pool:
                for res in pool.map(verify.match_batch, batches):
                    with db.tx() as c:
                        c.executemany("INSERT OR REPLACE INTO orb_cache(a, b, inliers, ratio, masked) VALUES(?,?,?,?,?)", res)
                    for a, b, inl, ratio, _ in res:
                        cache[(a, b)] = (inl, ratio)
                    done += len(res)
                    ctx.update(3, 4, f"ORB-verified {done:,} / {len(todo):,} pairs")
                    if ctx.cancelled():
                        break
        for i in need_idx:
            inl, ratio = cache.get((shas[A[i]], shas[B[i]]), (0, 0.0))
            orb_in[i], orb_ratio[i] = inl, ratio

    orb_ok = (orb_in >= st["orb_min_inliers"]) & (orb_ratio >= st["orb_min_ratio"])
    tier = np.full(len(A), "", dtype=object)
    tier[clip0 >= st["clip_similar"]] = "similar"
    tier[orb_ok & ((clip0 >= st["clip_near"]) | (phd <= st["phash_candidate"]))] = "near"
    tier[trim_near] = "near"
    tier[orb_ok & (clip0 >= st["clip_duplicate"])] = "duplicate"
    tier[hash_dup] = "duplicate"

    keep = np.nonzero(tier != "")[0]
    out_rows = [
        (
            shas[A[i]], shas[B[i]], int(phd[i]), int(dhd[i]), int(whd[i]), int(rot[i]), int(trim[i]),
            None if np.isnan(clip[i]) else round(float(clip[i]), 4),
            int(orb_in[i]) if need[i] else None, float(orb_ratio[i]) if need[i] else None, tier[i],
        )
        for i in keep
    ]
    with db.tx() as c:
        c.execute("DELETE FROM pairs")
        c.executemany(
            "INSERT INTO pairs(a, b, phash_d, dhash_d, whash_d, rot_d, trim_d, clip, orb_inliers, orb_ratio, tier)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            out_rows,
        )
    ctx.update(4, 4, f"{len(out_rows):,} similar pairs kept")
    return {"candidates": int(len(A)), "pairs": len(out_rows), "orb_checked": int(len(need_idx))}


def build_pairs(ctx) -> dict:
    """Visual pairs (methods 1-4), then OCR same-text pairs merged into the same table."""
    from . import ocr

    st = db.get_settings()
    result = _build_visual_pairs(ctx, st)
    tp = ocr.text_pairs(int(st["text_min_words"]), float(st["text_jaccard"]))
    with db.tx() as c:
        c.executemany(
            "INSERT INTO pairs(a, b, text_sim) VALUES(?,?,?) ON CONFLICT(a, b) DO UPDATE SET text_sim=excluded.text_sim",
            [(a, b, sim) for a, b, sim in tp],
        )
    result["text_pairs"] = len(tp)
    ctx.message(f"{result.get('pairs', 0):,} visual pairs, {len(tp):,} same-text pairs")
    return result
