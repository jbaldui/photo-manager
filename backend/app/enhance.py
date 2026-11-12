"""Enhance: merge several copies of the same picture into one better image.

1. load     every unique copy at full resolution (EXIF orientation applied)
2. align    each copy onto the best copy (the reference): ORB + RANSAC homography, refined with ECC.
            Copies that do not line up (different content, ECC correlation < 0.9) are dropped. A per-pixel
            mask keeps only the parts that match the reference (a caption present in one copy only is ignored).
3. fuse     robust weighted mean per pixel: values far from the per-pixel median (k x MAD) are rejected,
            each copy is weighted by sharpness, JPEG quality and native resolution. This removes noise and
            compression artefacts that the copies do not share.
4. upscale  optional Real-ESRGAN neural super-resolution (CPU, tiled), then resized to the requested scale.
5. save     into DATA_DIR/generated/<id>/ with the reference's EXIF date. Originals are never touched.

Super-resolution *invents* plausible detail: the result is for viewing and sharing, not a replacement original.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel

from . import config, db, decode, sr_models
from .grouping import _rep_key

MAX_SOURCES = 12
MAX_OUT_PIXELS = 40_000_000
SR_INPUT_CAP = {"fast": 4_000_000, "quality": 1_000_000}      # pixels fed to the network
SR_SEC_PER_MP = {"fast": 45.0, "quality": 560.0}             # measured on 4 CPU threads, scaled by WORKERS
FUSE_TIERS = ("exact", "duplicate", "near")
WORK_SIDE = 1024
ECC_MIN = 0.9


class EnhanceOptions(BaseModel):
    group_id: int | None = None
    file_ids: list[int] = []
    fuse: bool = True
    model: str = "fast"          # fast | quality | none
    scale: int = 2               # 1 | 2 | 4  (1 = same size, more detail)
    format: str = "auto"         # auto | jpg | png


class _Cancelled(Exception):
    pass


# ------------------------------------------------------------------ inputs
def _sources(o: EnhanceOptions) -> tuple[list[dict], str | None]:
    conn = db.get()
    tier = None
    if o.group_id is not None:
        g = conn.execute("SELECT tier FROM groups WHERE id=?", (o.group_id,)).fetchone()
        if not g:
            raise ValueError("Group not found")
        tier = g["tier"]
        ids = [r[0] for r in conn.execute("SELECT file_id FROM group_members WHERE group_id=?", (o.group_id,))]
        if o.file_ids:
            ids = [i for i in ids if i in set(o.file_ids)]
    else:
        ids = list(o.file_ids)
    if not ids:
        raise ValueError("Choose at least one photo")
    q = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT f.id, r.path AS root, f.rel_path, f.name, f.size, f.mtime, f.width, f.height, f.has_exif, f.date_source,
                   f.date_taken, f.sha256, f.format FROM files f JOIN roots r ON r.id=f.root_id
            WHERE f.status='ok' AND f.id IN ({q})""",
        ids,
    ).fetchall()
    by_sha: dict[str, dict] = {}
    for r in sorted(rows, key=_rep_key, reverse=True):      # best copy first; byte-identical copies add nothing
        by_sha.setdefault(r["sha256"], dict(r))
    files = list(by_sha.values())[:MAX_SOURCES]
    if not files:
        raise ValueError("None of these photos are available")
    return files, tier


def _plan_sizes(ref_w: int, ref_h: int, o: EnhanceOptions) -> dict:
    target_w, target_h = ref_w * o.scale, ref_h * o.scale
    if target_w * target_h > MAX_OUT_PIXELS:
        k = (MAX_OUT_PIXELS / (target_w * target_h)) ** 0.5
        target_w, target_h = int(target_w * k), int(target_h * k)
    sr_in = None
    if o.model in sr_models.MODELS:
        cap = SR_INPUT_CAP[o.model]
        if ref_w * ref_h <= cap:
            sr_in = (ref_w, ref_h)                         # network sees every pixel; output resized to target
        elif o.scale > 1:
            k = (cap / (ref_w * ref_h)) ** 0.5
            sr_in = (max(1, int(ref_w * k)), max(1, int(ref_h * k)))
        # scale 1 on an image larger than the cap: already high resolution, SR skipped
    return {"target": (target_w, target_h), "sr_input": sr_in}


def estimate(o: EnhanceOptions) -> dict:
    files, tier = _sources(o)
    ref = files[0]
    w, h = ref["width"] or 1, ref["height"] or 1
    fuse = o.fuse and len(files) > 1 and (tier in FUSE_TIERS or tier is None)
    sizes = _plan_sizes(w, h, o)
    sec = 2 + len(files) * (w * h / 1e6) * (3.0 if fuse else 0.4)
    if sizes["sr_input"]:
        mp = sizes["sr_input"][0] * sizes["sr_input"][1] / 1e6
        sec += mp * SR_SEC_PER_MP[o.model] * 4 / max(1, config.WORKERS)
    return {
        "sources": len(files), "fuse": fuse, "tier": tier, "reference": {"id": ref["id"], "rel_path": ref["rel_path"], "width": w, "height": h},
        "output": {"width": sizes["target"][0], "height": sizes["target"][1]},
        "super_resolution": bool(sizes["sr_input"]), "sr_input": sizes["sr_input"],
        "model_downloaded": sr_models.is_downloaded(o.model) if o.model in sr_models.MODELS else True,
        "seconds": round(sec),
        "notes": ([] if fuse or len(files) == 1 or not o.fuse else ["These photos are only *similar* (different shots): they are not merged; the best copy is enhanced."])
        + ([] if sizes["sr_input"] or o.model == "none" else ["The image is already larger than the network can improve at this scale; only fusion is applied."]),
    }


def load_full(path: str | Path) -> tuple[np.ndarray, Image.Image]:
    img = decode.open_image(path)
    try:
        img.seek(0)
    except Exception:
        pass
    src = img
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return np.asarray(img.convert("RGB")), src


def _jpeg_quality_factor(src: Image.Image) -> float:
    q = getattr(src, "quantization", None)
    if src.format != "JPEG" or not q:
        return 1.0
    mean_q = float(np.mean(q.get(0, list(q.values())[0])))
    return float(np.clip(1.0 - (mean_q - 1.0) / 60.0, 0.15, 1.0))


def _gray_work(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = rgb.shape[:2]
    s = min(1.0, WORK_SIDE / max(h, w))
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if s < 1:
        g = cv2.resize(g, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return g, s


def align(ref_rgb: np.ndarray, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, float] | None:
    """Warp `rgb` onto the reference frame. Returns (warped uint8, valid mask bool, ecc correlation) or None."""
    rg, sr = _gray_work(ref_rgb)
    og, so = _gray_work(rgb)
    orb = cv2.ORB_create(nfeatures=4000, scaleFactor=1.2, nlevels=8, edgeThreshold=15, patchSize=15, fastThreshold=7)
    kr, dr = orb.detectAndCompute(rg, None)
    ko, do = orb.detectAndCompute(og, None)
    H = None
    if dr is not None and do is not None and len(kr) >= 8 and len(ko) >= 8:
        knn = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(do, dr, k=2)
        good = [m[0] for m in knn if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
        if len(good) >= 12:
            src = np.float32([ko[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kr[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            if H is None or int(inl.sum()) < 12:
                H = None
    if H is None:
        # same picture, same framing (e.g. a plain re-encode): start from a pure rescale
        if abs(rgb.shape[1] / rgb.shape[0] - ref_rgb.shape[1] / ref_rgb.shape[0]) > 0.01:
            return None
        H = np.diag([rg.shape[1] / og.shape[1], rg.shape[0] / og.shape[0], 1.0])
    rh, rw = rg.shape
    pre = cv2.warpPerspective(og, H, (rw, rh), flags=cv2.INTER_LINEAR)
    foot = cv2.warpPerspective(np.full(og.shape, 255, np.uint8), H, (rw, rh), flags=cv2.INTER_NEAREST)
    foot = cv2.erode(foot, np.ones((5, 5), np.uint8))
    if foot.mean() < 255 * 0.2:
        return None
    W = np.eye(3, dtype=np.float32)
    try:
        cc, W = cv2.findTransformECC(rg.astype(np.float32), pre.astype(np.float32), W, cv2.MOTION_HOMOGRAPHY,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), foot, 5)
    except cv2.error:
        a, b = rg[foot > 0].astype(np.float32), pre[foot > 0].astype(np.float32)
        a, b = a - a.mean(), b - b.mean()
        cc = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))
        W = np.eye(3, dtype=np.float32)
    if cc < ECC_MIN:
        return None
    # ref_full -> other_full = S_o^-1 . H^-1 . W . S_r
    S_r = np.diag([sr, sr, 1.0])
    S_o_inv = np.diag([1 / so, 1 / so, 1.0])
    M = S_o_inv @ np.linalg.inv(H) @ W.astype(np.float64) @ S_r
    fh, fw = ref_rgb.shape[:2]
    warped = cv2.warpPerspective(rgb, M, (fw, fh), flags=cv2.INTER_LANCZOS4 | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)
    valid = cv2.warpPerspective(np.full(rgb.shape[:2], 255, np.uint8), M, (fw, fh), flags=cv2.INTER_NEAREST | cv2.WARP_INVERSE_MAP)
    valid = cv2.erode(valid, np.ones((3, 3), np.uint8)) > 0
    # content check against the reference: drop regions that differ (caption only in one copy, edits)
    d = cv2.GaussianBlur(cv2.absdiff(cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY), cv2.cvtColor(ref_rgb, cv2.COLOR_RGB2GRAY)), (0, 0), 3)
    valid &= d < 40
    return warped, valid, float(cc)


def sharpness(rgb: np.ndarray, mask: np.ndarray | None = None) -> float:
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    lap = cv2.Laplacian(g, cv2.CV_32F)
    return float(lap[mask].var()) if mask is not None and mask.any() else float(lap.var())


def fuse(frames: list[np.ndarray], masks: list[np.ndarray], weights: list[float], k: float = 3.5, tile: int = 512,
         cancelled=lambda: False) -> np.ndarray:
    """Robust per-pixel weighted mean. frames[0] (the reference) is valid everywhere."""
    h, w, _ = frames[0].shape
    out = np.empty((h, w, 3), np.uint8)
    wv = np.asarray(weights, np.float32).reshape(-1, 1, 1, 1)
    for y in range(0, h, tile):
        if cancelled():
            raise _Cancelled()
        for x in range(0, w, tile):
            sl = (slice(y, y + tile), slice(x, x + tile))
            st = np.stack([f[sl] for f in frames]).astype(np.float32)               # N,h,w,3
            m = np.stack([mk[sl] for mk in masks])[..., None]                        # N,h,w,1
            m = np.broadcast_to(m, st.shape)
            masked = np.where(m, st, np.nan)
            med = np.nanmedian(masked, axis=0)
            dev = np.abs(masked - med)
            mad = np.nanmedian(dev, axis=0)
            ok = m & (dev <= np.maximum(k * 1.4826 * mad, 4.0))
            wt = np.where(ok, wv, 0.0)
            num = (wt * np.nan_to_num(st)).sum(0)
            den = wt.sum(0)
            res = np.where(den > 0, num / np.maximum(den, 1e-6), st[0])
            out[sl] = np.clip(res + 0.5, 0, 255).astype(np.uint8)
    return out


# ------------------------------------------------------------------ job
class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.state: dict = {"running": False}

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def status(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw)

    def start(self, o: EnhanceOptions) -> dict:
        from .locks import ensure_idle

        ensure_idle("enhance")
        if o.model not in (*sr_models.MODELS, "none") or o.scale not in (1, 2, 4) or o.format not in ("auto", "jpg", "png"):
            raise ValueError("Invalid options")
        est = estimate(o)
        if not est["fuse"] and not est["super_resolution"]:
            raise ValueError("Nothing to do: choose super-resolution or at least two copies to merge")
        self.cancel.clear()
        self.state = {"running": True, "phase": "starting", "progress": 0.0, "message": "", "error": None, "result": None,
                      "started": time.time(), "finished": None, "estimate": est, "cancelled": False}
        self.thread = threading.Thread(target=self._run, args=(o, est), daemon=True, name="enhance")
        self.thread.start()
        return self.status()

    def run_sync(self, o: EnhanceOptions) -> dict:
        est = estimate(o)
        self.cancel.clear()
        self.state = {"running": True, "phase": "starting", "progress": 0.0, "message": "", "error": None, "result": None,
                      "started": time.time(), "finished": None, "estimate": est, "cancelled": False}
        self._run(o, est)
        return self.status()

    def _run(self, o: EnhanceOptions, est: dict) -> None:
        try:
            self._set(result=self._enhance(o, est), phase="done", progress=1.0)
        except (_Cancelled, InterruptedError):
            self._set(cancelled=True, phase="cancelled")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._set(error=f"{type(e).__name__}: {e}", phase="error")
        finally:
            self._set(running=False, finished=time.time())

    def _enhance(self, o: EnhanceOptions, est: dict) -> dict:
        files, tier = _sources(o)
        cancelled = self.cancel.is_set
        ref_f = files[0]
        self._set(phase="loading", message=f"Loading {len(files)} photo(s)")
        ref_rgb, ref_src = load_full(Path(ref_f["root"]) / ref_f["rel_path"])
        ref_exif = ref_src.getexif()
        used = [{"id": ref_f["id"], "rel_path": ref_f["rel_path"], "role": "reference"}]
        img = ref_rgb
        if est["fuse"]:
            frames, masks, weights = [ref_rgb], [np.ones(ref_rgb.shape[:2], bool)], []
            ref_px = ref_rgb.shape[0] * ref_rgb.shape[1]
            quals = [_jpeg_quality_factor(ref_src)]
            natives = [1.0]
            for i, f in enumerate(files[1:], 1):
                if cancelled():
                    raise _Cancelled()
                self._set(phase="aligning", message=f"Aligning copy {i} of {len(files) - 1}", progress=0.05 + 0.3 * i / len(files))
                try:
                    rgb, src = load_full(Path(f["root"]) / f["rel_path"])
                    res = align(ref_rgb, rgb)
                except Exception as e:  # noqa: BLE001
                    used.append({"id": f["id"], "rel_path": f["rel_path"], "role": f"skipped ({type(e).__name__})"})
                    continue
                if res is None:
                    used.append({"id": f["id"], "rel_path": f["rel_path"], "role": "skipped (does not line up)"})
                    continue
                warped, valid, cc = res
                frames.append(warped)
                masks.append(valid)
                quals.append(_jpeg_quality_factor(src))
                natives.append(min(1.0, (rgb.shape[0] * rgb.shape[1] / ref_px) ** 0.5))
                used.append({"id": f["id"], "rel_path": f["rel_path"], "role": "merged", "ecc": round(cc, 4),
                             "coverage": round(float(valid.mean()), 3)})
            if len(frames) > 1:
                sharp = [sharpness(fr, mk) for fr, mk in zip(frames, masks)]
                top = max(sharp) or 1.0
                weights = [max(0.05, (s / top) ** 0.5) * q * n for s, q, n in zip(sharp, quals, natives)]
                self._set(phase="fusing", message=f"Merging {len(frames)} aligned copies", progress=0.4)
                img = fuse(frames, masks, weights, cancelled=cancelled)
            del frames, masks
        sizes = _plan_sizes(img.shape[1], img.shape[0], o)
        tw, th = sizes["target"]
        if sizes["sr_input"]:
            if not sr_models.is_downloaded(o.model):
                self._set(phase="downloading", message=f"Downloading {sr_models.MODELS[o.model]['label']} (one time)")
                sr_models.ensure_weights(o.model, lambda d, t: self._set(message=f"Downloading model {d / 1e6:.0f} / {t / 1e6:.0f} MB"))
            iw, ih = sizes["sr_input"]
            src_img = img if (iw, ih) == (img.shape[1], img.shape[0]) else cv2.resize(img, (iw, ih), interpolation=cv2.INTER_AREA)
            self._set(phase="upscaling", message="Neural super-resolution", progress=0.5)
            img = sr_models.upscale(
                src_img, o.model,
                progress=lambda n, t: self._set(progress=0.5 + 0.45 * n / t, message=f"Neural super-resolution: tile {n} of {t}"),
                cancelled=cancelled,
            )
        if (img.shape[1], img.shape[0]) != (tw, th):
            interp = cv2.INTER_AREA if img.shape[1] > tw else cv2.INTER_LANCZOS4
            img = cv2.resize(img, (tw, th), interpolation=interp)
        self._set(phase="saving", message="Saving", progress=0.97)
        return _save(img, ref_f, ref_exif, o, est, used, tier)


def _save(img: np.ndarray, ref_f: dict, ref_exif, o: EnhanceOptions, est: dict, used: list, tier: str | None) -> dict:
    fmt = o.format if o.format != "auto" else ("jpg" if (ref_f["format"] or "").upper() in ("JPEG", "MPO") else "png")
    with db.tx() as c:
        gid = c.execute(
            "INSERT INTO generated(group_id, source_file_ids, path, params_json, created, width, height) VALUES(?,?,?,?,?,?,?)",
            (o.group_id, json.dumps([u["id"] for u in used if u["role"] in ("reference", "merged")]), "",
             json.dumps({**o.model_dump(), "sources": used, "fused": est["fuse"], "tier": tier}), time.time(), img.shape[1], img.shape[0]),
        ).lastrowid
    out_dir = config.GENERATED_DIR / str(gid)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{Path(ref_f['name']).stem[:80]}_enhanced.{fmt}"
    exif = Image.Exif()
    for k, v in ref_exif.items():
        if isinstance(v, (str, int, float, bytes)) and k not in (0x0112, 0x0131):
            exif[k] = v
    try:
        for k, v in ref_exif.get_ifd(0x8769).items():          # Exif IFD: DateTimeOriginal etc.
            if isinstance(v, (str, int, float, bytes)):
                exif.get_ifd(0x8769)[k] = v
    except Exception:
        pass
    exif[0x0112] = 1
    exif[0x0131] = "Photo Manager Enhance"
    pil = Image.fromarray(img)
    try:
        if fmt == "jpg":
            pil.save(out_dir / name, quality=95, subsampling=0, exif=exif)
        else:
            pil.save(out_dir / name, exif=exif, compress_level=6)
    except Exception:
        pil.save(out_dir / name, **({"quality": 95} if fmt == "jpg" else {}))
    with db.tx() as c:
        c.execute("UPDATE generated SET path=? WHERE id=?", (f"{gid}/{name}", gid))
    return get(gid)


def get(gid: int) -> dict | None:
    r = db.get().execute("SELECT * FROM generated WHERE id=?", (gid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["params"] = json.loads(d.pop("params_json") or "{}")
    d["source_file_ids"] = json.loads(d["source_file_ids"])
    p = config.GENERATED_DIR / d["path"]
    d["bytes"] = p.stat().st_size if d["path"] and p.is_file() else None
    d["name"] = Path(d["path"]).name
    d["image_url"] = f"/api/generated/{gid}/image"
    return d


def list_all() -> list[dict]:
    return [get(r[0]) for r in db.get().execute("SELECT id FROM generated WHERE path<>'' ORDER BY id DESC")]


def delete(gid: int) -> bool:
    d = get(gid)
    if not d:
        return False
    folder = (config.GENERATED_DIR / str(gid)).resolve()
    if config.GENERATED_DIR.resolve() in folder.parents and folder.is_dir():
        shutil.rmtree(folder)
    with db.tx() as c:
        c.execute("DELETE FROM generated WHERE id=?", (gid,))
    return True


def save_copy(gid: int, dest: str) -> str:
    from .paths import check_outside_library

    d = get(gid)
    if not d:
        raise KeyError(gid)
    folder = check_outside_library(dest)
    src = config.GENERATED_DIR / d["path"]
    stem, ext = Path(d["name"]).stem, Path(d["name"]).suffix
    target, n = folder / d["name"], 2
    while target.exists():
        target = folder / f"{stem}_{n}{ext}"
        n += 1
    shutil.copy2(src, target)
    return str(target)


job = Job()
