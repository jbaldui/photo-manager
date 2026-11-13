"""Stage: face detection + recognition, fully local, using models that ship through OpenCV.

- Detector: YuNet (opencv_zoo, ~230 KB ONNX).   - Recognizer: SFace (opencv_zoo, ~37 MB ONNX, 128-d).
- No extra Python dependency (cv2 is already required) and permissive licences.
- Results: `faces` (one row per detected face, embedding as float32 BLOB) and `faces_done` (images
  already processed, including those with zero faces) so the stage is resumable and incremental.
- Clustering turns faces into `people`. Faces a user assigned by hand (`manual=1`) are never moved.
"""
from __future__ import annotations

import multiprocessing as mp
import time
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np

from . import config, db, decode

FACE_DIR = config.MODEL_DIR / "faces"
_BASE = "https://github.com/opencv/opencv_zoo/raw/main/models/"
MODELS = {
    "det": ("face_detection_yunet_2023mar.onnx", _BASE + "face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "rec": ("face_recognition_sface_2021dec.onnx", _BASE + "face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}
READ_SIZE = 1280        # image decoded at this size (long side) before detection
MIN_SCORE = 0.8         # detector confidence
MIN_FACE_PX = 36        # smaller faces (in the READ_SIZE image) are too blurry to recognise
EMB_DIM = 128

# Clustering defaults; overridable through the `settings`-style kv table (see get_threshold()).
DEFAULT_THRESHOLD = 0.5   # cosine similarity to link two faces (SFace's own "same person" cut-off is 0.363)
KNN = 12


def ensure_models() -> dict[str, str]:
    FACE_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for key, (name, url) in MODELS.items():
        p = FACE_DIR / name
        if not p.exists() or p.stat().st_size < 1000:
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)  # noqa: S310  (fixed https URL)
            tmp.replace(p)
        out[key] = str(p)
    return out


_ENGINE: tuple | None = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        import cv2

        cv2.setNumThreads(1)
        m = ensure_models()
        det = cv2.FaceDetectorYN.create(m["det"], "", (320, 320), MIN_SCORE, 0.3, 100)
        rec = cv2.FaceRecognizerSF.create(m["rec"], "")
        _ENGINE = (det, rec)
    return _ENGINE


def init_worker() -> None:
    _engine()


def detect_faces(path: str) -> dict:
    """Read-only. Returns {'faces': [{'box': [x0,y0,x1,y1] normalised, 'score', 'emb': float32[128]}]}."""
    try:
        img = decode.to_working_rgb(decode.open_image(path), READ_SIZE)
        return {"faces": faces_in_array(np.ascontiguousarray(np.asarray(img)[:, :, ::-1]))}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def faces_in_array(bgr: np.ndarray) -> list[dict]:
    det, rec = _engine()
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, rows = det.detect(bgr)
    out = []
    for r in rows if rows is not None else []:
        x, y, fw, fh = (float(v) for v in r[:4])
        if min(fw, fh) < MIN_FACE_PX:
            continue
        emb = rec.feature(rec.alignCrop(bgr, r)).reshape(-1).astype(np.float32)
        n = float(np.linalg.norm(emb))
        if n == 0:
            continue
        out.append({
            "box": [max(0.0, x / w), max(0.0, y / h), min(1.0, (x + fw) / w), min(1.0, (y + fh) / h)],
            "score": float(r[-1]),
            "emb": emb / n,
        })
    return out


def detect_batch(items: list[tuple[str, str]]) -> list[tuple[str, dict]]:
    return [(sha, detect_faces(p)) for sha, p in items]


def _write(results: list[tuple[str, dict]]) -> None:
    now = time.time()
    with db.tx() as c:
        for sha, res in results:
            if "error" in res:
                c.execute("INSERT OR REPLACE INTO faces_done(sha256, n_faces, error, done_at) VALUES(?,0,?,?)", (sha, res["error"], now))
                continue
            c.execute("DELETE FROM faces WHERE sha256=? AND manual=0", (sha,))
            for f in res["faces"]:
                x0, y0, x1, y1 = f["box"]
                c.execute(
                    "INSERT INTO faces(sha256, x0, y0, x1, y1, score, emb) VALUES(?,?,?,?,?,?,?)",
                    (sha, x0, y0, x1, y1, f["score"], f["emb"].tobytes()),
                )
            c.execute("INSERT OR REPLACE INTO faces_done(sha256, n_faces, error, done_at) VALUES(?,?,NULL,?)", (sha, len(res["faces"]), now))


def faces_pending(ctx) -> dict:
    conn = db.get()
    rows = conn.execute(
        """SELECT i.sha256, MIN(r.path || '/' || f.rel_path) AS path
           FROM images i JOIN files f ON f.sha256=i.sha256 AND f.status='ok' JOIN roots r ON r.id=f.root_id
           WHERE NOT EXISTS (SELECT 1 FROM faces_done d WHERE d.sha256=i.sha256)
           GROUP BY i.sha256"""
    ).fetchall()
    total = len(rows)
    ctx.update(0, total, f"Faces: {total:,} images to scan")
    if not total:
        return {"scanned": 0, "faces": 0, "people": cluster(ctx)}
    ctx.update(0, total, "Loading face models (first run downloads ~37 MB)")
    ensure_models()
    jobs = [[(r["sha256"], r["path"]) for r in rows[i : i + 8]] for i in range(0, total, 8)]
    done = found = 0
    buf: list[tuple[str, dict]] = []
    last = time.time()
    with ProcessPoolExecutor(max_workers=config.WORKERS, mp_context=mp.get_context("spawn"), initializer=init_worker) as pool:
        it = iter(jobs)
        inflight = {pool.submit(detect_batch, j) for j in (next(it, None) for _ in range(config.WORKERS * 2)) if j}
        while inflight:
            fin, inflight = wait(inflight, return_when=FIRST_COMPLETED)
            for f in fin:
                res = f.result()
                buf.extend(res)
                done += len(res)
                found += sum(len(r.get("faces", [])) for _, r in res)
                if not ctx.cancelled():
                    j = next(it, None)
                    if j:
                        inflight.add(pool.submit(detect_batch, j))
            if len(buf) >= 64 or time.time() - last > 3:
                _write(buf)
                buf.clear()
                last = time.time()
            ctx.update(done, total, f"Faces: scanned {done:,} / {total:,} images · {found:,} faces")
    _write(buf)
    return {"scanned": done, "faces": found, "people": cluster(ctx) if not ctx.cancelled() else None}


# ----------------------------------------------------------------- clustering
def get_threshold() -> float:
    r = db.get().execute("SELECT value FROM kv WHERE key='face_threshold'").fetchone()
    return float(r["value"]) if r else DEFAULT_THRESHOLD


def set_threshold(v: float) -> None:
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES('face_threshold', ?)", (str(float(v)),))


def _load(where: str = "1") -> tuple[list[int], np.ndarray]:
    rows = db.get().execute(f"SELECT id, emb FROM faces WHERE {where}").fetchall()
    if not rows:
        return [], np.zeros((0, EMB_DIM), np.float32)
    return [r["id"] for r in rows], np.stack([np.frombuffer(r["emb"], dtype=np.float32) for r in rows])


def cluster(ctx=None) -> dict:
    """(Re)assign every non-manual face to a person.

    1. Faces whose person was set by hand (`manual=1`) stay put and define each person's centroid.
    2. Other faces join the best-matching existing *named* person when similar enough.
    3. The rest are grouped by connected components over mutual k-NN edges above the threshold,
       and become new unnamed people. Singletons stay unassigned (person_id NULL).
    """
    import faiss

    thr = get_threshold()
    conn = db.get()
    ids, X = _load()
    if not ids:
        return {"people": 0, "assigned": 0}
    id_pos = {fid: i for i, fid in enumerate(ids)}
    man = conn.execute("SELECT id, person_id FROM faces WHERE manual=1 AND person_id IS NOT NULL").fetchall()
    manual_ids = {r["id"] for r in man}
    # centroids of people that have manual/named faces
    cent: dict[int, list[np.ndarray]] = {}
    for r in man:
        cent.setdefault(r["person_id"], []).append(X[id_pos[r["id"]]])
    named = {r["id"] for r in conn.execute("SELECT id FROM people WHERE name IS NOT NULL")}
    for r in conn.execute("SELECT id, person_id FROM faces WHERE person_id IN (SELECT id FROM people WHERE name IS NOT NULL) AND manual=0"):
        cent.setdefault(r["person_id"], []).append(X[id_pos[r["id"]]])
    cent_ids = [p for p in cent if p in named]
    C = np.stack([np.mean(cent[p], axis=0) for p in cent_ids]) if cent_ids else np.zeros((0, EMB_DIM), np.float32)
    if len(C):
        C /= np.linalg.norm(C, axis=1, keepdims=True)

    assign: dict[int, int | None] = {}
    free = [fid for fid in ids if fid not in manual_ids]
    rest: list[int] = []
    for fid in free:
        if len(C):
            s = C @ X[id_pos[fid]]
            j = int(s.argmax())
            if s[j] >= thr:
                assign[fid] = cent_ids[j]
                continue
        rest.append(fid)

    # connected components among the rest
    if rest:
        R = np.ascontiguousarray(X[[id_pos[f] for f in rest]])
        index = faiss.IndexFlatIP(EMB_DIM)
        index.add(R)
        k = min(KNN + 1, len(rest))
        sims, nbrs = index.search(R, k)
        parent = list(range(len(rest)))

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        nb_sets = [set(nbrs[i][sims[i] >= thr].tolist()) for i in range(len(rest))]
        for i in range(len(rest)):
            for j in nb_sets[i]:
                if j != i and i in nb_sets[j]:      # mutual neighbours only: limits single-link chaining
                    parent[find(i)] = find(j)
        comps: dict[int, list[int]] = {}
        for i in range(len(rest)):
            comps.setdefault(find(i), []).append(i)
        # reuse ids of existing *unnamed* people where possible so URLs/ordering stay stable
        old = {r["id"] for r in conn.execute("SELECT id FROM people WHERE name IS NULL")}
        prev: dict[int, int] = {}
        for r in conn.execute("SELECT id, person_id FROM faces WHERE person_id IS NOT NULL"):
            prev[r["id"]] = r["person_id"]
        with db.tx() as c:
            used: set[int] = set()
            for members in sorted(comps.values(), key=len, reverse=True):
                if len(members) < 2:
                    for i in members:
                        assign[rest[i]] = None
                    continue
                votes: dict[int, int] = {}
                for i in members:
                    p = prev.get(rest[i])
                    if p in old and p not in used:
                        votes[p] = votes.get(p, 0) + 1
                pid = max(votes, key=votes.get) if votes else c.execute("INSERT INTO people(name, created) VALUES(NULL, ?)", (time.time(),)).lastrowid
                used.add(pid)
                for i in members:
                    assign[rest[i]] = pid
    with db.tx() as c:
        c.executemany("UPDATE faces SET person_id=? WHERE id=?", [(p, f) for f, p in assign.items()])
        # drop unnamed people that lost all their faces
        c.execute("DELETE FROM people WHERE name IS NULL AND id NOT IN (SELECT DISTINCT person_id FROM faces WHERE person_id IS NOT NULL)")
        # cover face = highest-scoring face
        c.execute(
            """UPDATE people SET cover_face_id=(SELECT id FROM faces WHERE person_id=people.id ORDER BY (x1-x0)*(y1-y0)*score DESC LIMIT 1)"""
        )
    n_people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    return {"people": n_people, "assigned": sum(1 for v in assign.values() if v is not None)}
