"""ORB keypoint matching + RANSAC homography: geometric confirmation that two images share content.

Keypoints inside OCR text boxes are ignored, so two *different* pictures with the same caption
(very common with memes) are not "confirmed" just because their text matches.

Robust to crops, rotation, scaling, captions and watermarks; rejects pairs that are merely
semantically alike (two different beach photos score high on CLIP but fail here).
"""
from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from . import config

cv2.setNumThreads(1)
_ORB = None


def _orb():
    global _ORB
    if _ORB is None:
        _ORB = cv2.ORB_create(nfeatures=1000, scaleFactor=1.2, nlevels=6, edgeThreshold=15, patchSize=15, fastThreshold=10)
    return _ORB


Boxes = tuple[tuple[float, float, float, float], ...]


@lru_cache(maxsize=4096)
def features(thumb_rel: str, text_boxes: Boxes = ()):
    img = cv2.imread(str(config.THUMB_DIR / thumb_rel), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None, None
    h, w = img.shape
    scale = 512 / max(h, w)
    if scale > 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    mask = None
    if text_boxes:
        h, w = img.shape
        mask = np.full((h, w), 255, dtype=np.uint8)
        pad = 0.02
        for x0, y0, x1, y1 in text_boxes:
            mask[max(0, int((y0 - pad) * h)) : int((y1 + pad) * h) + 1, max(0, int((x0 - pad) * w)) : int((x1 + pad) * w) + 1] = 0
    kp, des = _orb().detectAndCompute(img, mask)
    if des is None or len(kp) < 8:
        return None, None
    pts = np.float32([k.pt for k in kp])
    return pts, des


def match(thumb_a: str, thumb_b: str, boxes_a: Boxes = (), boxes_b: Boxes = ()) -> tuple[int, float]:
    """Returns (RANSAC inlier count, inliers / min(keypoints))."""
    pa, da = features(thumb_a, boxes_a)
    pb, db_ = features(thumb_b, boxes_b)
    if da is None or db_ is None:
        return 0, 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = bf.knnMatch(da, db_, k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
    if len(good) < 8:
        return len(good) // 4, 0.0
    src = pa[[m.queryIdx for m in good]].reshape(-1, 1, 2)
    dst = pb[[m.trainIdx for m in good]].reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    if mask is None:
        return 0, 0.0
    inliers = int(mask.sum())
    return inliers, round(inliers / max(1, min(len(pa), len(pb))), 4)


def match_batch(pairs: list[tuple]) -> list[tuple[str, str, int, float, int]]:
    """pairs: (sha_a, sha_b, thumb_a, thumb_b, text_boxes_a, text_boxes_b) -> (a, b, inliers, ratio, masked)."""
    out = []
    for a, b, ta, tb, ba, bb in pairs:
        try:
            inl, ratio = match(ta, tb, ba, bb)
        except Exception:
            inl, ratio = 0, 0.0
        out.append((a, b, inl, ratio, int(bool(ba or bb))))
    return out
