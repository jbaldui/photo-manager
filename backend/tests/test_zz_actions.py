"""Actions that change the library: quarantine (+ undo), Auto Process, Enhance.

Runs after test_pipeline (file name order) on its *own* copy of the fixtures, so the read-only guarantees
checked there are unaffected. All existing folders are removed first.
"""
import errno
import hashlib
import io
import os
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _snapshot(d: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(d.rglob("*"))}


def _wait(client, url):
    for _ in range(1200):
        st = client.get(url).json()
        if not st.get("running"):
            return st
        time.sleep(0.2)
    raise AssertionError(f"{url} did not finish")


def _scan(client, stages=None):
    r = client.post("/api/scan", json={"stages": stages} if stages else {})
    assert r.status_code == 200, r.text
    st = _wait(client, "/api/scan/status")
    assert st["error"] is None, st["error"]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    from app.main import app
    from tests.make_fixtures import make

    client = TestClient(app)
    for r in client.get("/api/roots").json():
        assert client.delete(f"/api/roots/{r['id']}").status_code == 200
    photos = tmp_path_factory.mktemp("actions") / "photos"
    make(photos)
    assert client.post("/api/roots", json={"path": str(photos)}).status_code == 200
    _scan(client)
    return client, photos


def _file(client, name):
    items = client.get("/api/photos", params={"q": name, "status": "all", "limit": 50}).json()["items"]
    return next(i for i in items if Path(i["rel_path"]).name == name)


def _exact_group_with(client, name):
    for g in client.get("/api/groups", params={"tier": "exact"}).json()["items"]:
        d = client.get(f"/api/groups/{g['id']}").json()
        if any(Path(m["rel_path"]).name == name for m in d["members"]):
            return d
    return None


def _tiers(client):
    return {k: v["groups"] for k, v in client.get("/api/stats").json()["tiers"].items()}


# ---------------------------------------------------------------- quarantine
def test_quarantine_rejects_dest_inside_library(lib):
    client, photos = lib
    r = client.post("/api/removals/preview", json={"dest": str(photos / "sub")})
    assert r.status_code == 400 and "scanned folder" in r.json()["detail"]


def test_quarantine_move_and_restore(lib, tmp_path):
    client, photos = lib
    g = _exact_group_with(client, "download_b.jpg_large")
    assert g is not None
    dup = next(m for m in g["members"] if m["rel_path"] == "download_b.jpg_large")
    keep = next(m for m in g["members"] if m["id"] != dup["id"])
    client.put("/api/decisions", json={"file_ids": [dup["id"]], "decision": "discard"})
    client.put("/api/decisions", json={"file_ids": [keep["id"]], "decision": "keep"})
    src = photos / "download_b.jpg_large"
    sha, mtime = _sha(src), src.stat().st_mtime_ns
    tiers_before = _tiers(client)

    pv = client.post("/api/removals/preview", json={"dest": str(tmp_path / "q"), "scope": "discarded"}).json()
    assert pv["files"] == 1 and pv["blocked"] == [] and pv["dest_exists"] is False
    assert client.post("/api/removals/start", json={"dest": str(tmp_path / "q")}).status_code == 200
    st = _wait(client, "/api/removals/status")
    assert st["moved"] == 1 and not st["errors"]

    # moved, identical, out of every group, UI data updated without a rescan
    assert not src.exists()
    moved = list((tmp_path / "q").rglob("download_b.jpg_large"))
    assert len(moved) == 1 and _sha(moved[0]) == sha
    assert (moved[0].parents[1] / "manifest.csv").is_file()
    assert _exact_group_with(client, "b.jpg") is None
    assert _tiers(client).get("exact", 0) == tiers_before["exact"] - 1
    assert _file(client, "download_b.jpg_large")["status"] == "removed"
    assert client.get("/api/stats").json()["removed"] == 1

    # a rescan must not flip it to "missing"
    _scan(client, ["scan", "group"])
    assert _file(client, "download_b.jpg_large")["status"] == "removed"

    batch = client.get("/api/removals").json()[0]
    assert batch["in_quarantine"] == 1 and batch["reason"] == "manual"
    assert client.post(f"/api/removals/{batch['id']}/restore").status_code == 200
    st = _wait(client, "/api/removals/status")
    assert st["moved"] == 1 and st["conflicts"] == 0
    assert src.is_file() and _sha(src) == sha and src.stat().st_mtime_ns == mtime
    assert _file(client, "download_b.jpg_large")["status"] == "ok"
    assert _tiers(client) == tiers_before
    assert not any((tmp_path / "q").rglob("*.jpg_large"))
    client.put("/api/decisions", json={"file_ids": [dup["id"], keep["id"]], "decision": "clear"})


def test_quarantine_never_moves_last_copy(lib, tmp_path):
    client, _ = lib
    g = _exact_group_with(client, "download_b.jpg_large")
    ids = [m["id"] for m in g["members"]]
    client.put("/api/decisions", json={"file_ids": ids, "decision": "discard"})
    pv = client.post("/api/removals/preview", json={"dest": str(tmp_path), "group_id": g["id"]}).json()
    assert pv["files"] == len(ids) - 1 and len(pv["blocked"]) == 1
    assert pv["blocked"][0]["file_id"] == g["rep_file_id"]
    client.put("/api/decisions", json={"file_ids": ids, "decision": "clear"})


def test_restore_conflict_then_retry(lib, tmp_path):
    client, photos = lib
    f = _file(client, "download_b.jpg_large")
    src = photos / f["rel_path"]
    original = src.read_bytes()
    client.put("/api/decisions", json={"file_ids": [f["id"]], "decision": "discard"})
    client.post("/api/removals/start", json={"dest": str(tmp_path)})
    _wait(client, "/api/removals/status")
    assert not src.exists()
    src.write_bytes(b"someone put a different file here")
    bid = client.get("/api/removals").json()[0]["id"]
    client.post(f"/api/removals/{bid}/restore")
    st = _wait(client, "/api/removals/status")
    assert st["conflicts"] == 1 and src.read_bytes() == b"someone put a different file here"   # never overwritten
    assert client.get(f"/api/removals/{bid}").json()["items"][0]["status"] == "conflict"
    src.unlink()
    client.post(f"/api/removals/{bid}/restore")
    st = _wait(client, "/api/removals/status")
    assert st["moved"] == 1 and src.read_bytes() == original
    assert client.get("/api/removals").json()[0]["state"] == "restored"
    client.put("/api/decisions", json={"file_ids": [f["id"]], "decision": "clear"})


def test_quarantine_cross_device_fallback(lib, tmp_path, monkeypatch):
    from app import quarantine

    client, photos = lib
    real_rename = os.rename

    def no_rename(a, b):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(quarantine.os, "rename", no_rename)
    f = _file(client, "download_b.jpg_large")
    src = photos / f["rel_path"]
    sha = _sha(src)
    client.put("/api/decisions", json={"file_ids": [f["id"]], "decision": "discard"})
    client.post("/api/removals/start", json={"dest": str(tmp_path)})
    st = _wait(client, "/api/removals/status")
    assert st["moved"] == 1 and not src.exists()
    moved = next(tmp_path.rglob("download_b.jpg_large"))
    assert _sha(moved) == sha and not list(tmp_path.rglob("*.partial"))
    monkeypatch.setattr(quarantine.os, "rename", real_rename)
    client.post(f"/api/removals/{client.get('/api/removals').json()[0]['id']}/restore")
    _wait(client, "/api/removals/status")
    assert src.is_file()
    client.put("/api/decisions", json={"file_ids": [f["id"]], "decision": "clear"})


# ---------------------------------------------------------------- auto process
def _pipeline(client, name):
    return next(p for p in client.get("/api/pipelines").json() if p["name"] == name)


def test_auto_rejects_unsafe_tiers(lib):
    client, _ = lib
    cfg = {"decide": {"enabled": True, "tiers": ["exact", "near"]}}
    assert client.post("/api/pipelines", json={"name": "bad", "config": cfg}).status_code == 422


def test_auto_dry_run_changes_nothing(lib, tmp_path):
    client, photos = lib
    p = _pipeline(client, "Clean & organize")
    cfg = p["config"]
    cfg["organize"]["options"]["dest"] = str(tmp_path / "out")
    cfg["quarantine"]["dest"] = str(tmp_path / "q")
    (tmp_path / "out").mkdir()
    assert client.put(f"/api/pipelines/{p['id']}", json={"name": p["name"], "config": cfg}).status_code == 200
    before, tiers = _snapshot(photos), _tiers(client)
    r = client.post(f"/api/pipelines/{p['id']}/run", json={"dry_run": True})
    assert r.status_code == 200, r.text
    st = _wait(client, "/api/auto/status")
    assert st["error"] is None, st
    steps = {s["key"]: s for s in st["steps"]}
    assert steps["decide"]["result"]["discarded"] > 0
    assert steps["organize"]["result"]["files"] > 0
    assert steps["quarantine"]["result"]["files"] == steps["decide"]["result"]["discarded"]
    assert _snapshot(photos) == before and _tiers(client) == tiers
    assert not list((tmp_path / "out").iterdir()) and not (tmp_path / "q").exists()
    assert client.get("/api/stats").json()["decisions"] == []


def test_auto_clean_and_organize(lib, tmp_path):
    client, photos = lib
    p = _pipeline(client, "Clean & organize")
    cfg = p["config"]
    (tmp_path / "out").mkdir()
    cfg["organize"]["options"]["dest"] = str(tmp_path / "out")
    cfg["quarantine"]["dest"] = str(tmp_path / "q")
    client.put(f"/api/pipelines/{p['id']}", json={"name": p["name"], "config": cfg})
    tiers_before = _tiers(client)
    assert tiers_before.get("exact") and tiers_before.get("duplicate") and tiers_before.get("near")
    client.post(f"/api/pipelines/{p['id']}/run", json={"dry_run": False})
    st = _wait(client, "/api/auto/status")
    assert st["error"] is None, st
    steps = {s["key"]: s["result"] for s in st["steps"]}
    after = _tiers(client)
    assert after.get("exact", 0) == 0 and after.get("duplicate", 0) == 0     # nothing left to clean
    assert after.get("near", 0) >= 1                                           # needs a human: untouched
    assert steps["quarantine"]["files"] == steps["decide"]["discarded"] > 0
    organized = next((tmp_path / "out").iterdir())
    assert (organized / "manifest.csv").is_file()
    copies = [x for x in organized.rglob("*") if x.is_file() and x.name != "manifest.csv"]
    assert len(copies) == steps["organize"]["files"] == client.get("/api/stats").json()["total_files"]
    batch = client.get("/api/removals").json()[0]
    assert batch["reason"] == "auto:Clean & organize" and batch["in_quarantine"] == steps["quarantine"]["files"]
    assert _pipeline(client, "Clean & organize")["last_result"]["steps"]

    # one click undo brings every duplicate back
    client.post(f"/api/removals/{batch['id']}/restore")
    _wait(client, "/api/removals/status")
    assert _tiers(client) == tiers_before


# ---------------------------------------------------------------- enhance
def _degraded_copies(n=5):
    from tests.make_fixtures import synthetic_photo

    clean = synthetic_photo(3, size=(800, 600))
    rng = np.random.default_rng(1)
    out = []
    for i in range(n):
        im = clean
        if i % 2:
            im = im.rotate(0.7 * i, resample=Image.BICUBIC, fillcolor=(128, 128, 128))
        if i:
            im = im.crop((i * 3, i * 2, 800, 600)).resize((800 - i * 3, 600 - i * 2))
        a = np.asarray(im).astype(np.float32) + rng.normal(0, 8, (im.height, im.width, 3))
        buf = io.BytesIO()
        Image.fromarray(a.clip(0, 255).astype(np.uint8)).save(buf, "JPEG", quality=30)
        buf.seek(0)
        out.append(np.asarray(Image.open(buf).convert("RGB")))
    return np.asarray(clean).astype(np.float32), out


def test_enhance_fusion_beats_every_single_copy():
    from app import enhance

    clean, copies = _degraded_copies()
    region = (slice(60, 540), slice(60, 740))

    def psnr(x):
        return 10 * np.log10(255**2 / np.mean((x[region].astype(np.float32) - clean[region]) ** 2))

    ref = copies[0]
    frames, masks = [ref], [np.ones(ref.shape[:2], bool)]
    for c in copies[1:]:
        r = enhance.align(ref, c)
        assert r is not None and r[2] > 0.95
        frames.append(r[0])
        masks.append(r[1])
    fused = enhance.fuse(frames, masks, [1.0] * len(frames))
    assert psnr(fused) > max(psnr(f) for f in frames) + 1.0


def test_enhance_rejects_unrelated_image():
    from app import enhance
    from tests.make_fixtures import synthetic_photo

    _, copies = _degraded_copies(1)
    assert enhance.align(copies[0], np.asarray(synthetic_photo(99, size=(800, 600)))) is None


def test_super_resolution_x4():
    from app import sr_models

    img = (np.random.default_rng(0).random((40, 56, 3)) * 255).astype(np.uint8)
    out = sr_models.upscale(img, "fast", tile=32, pad=8)
    assert out.shape == (160, 224, 3) and out.dtype == np.uint8


def test_enhance_group_api(lib):
    client, photos = lib
    before = _snapshot(photos)
    g = next(x for x in client.get("/api/groups", params={"tier": "duplicate"}).json()["items"])
    est = client.post("/api/enhance/estimate", json={"group_id": g["id"], "model": "none", "scale": 1}).json()
    assert est["fuse"] and est["sources"] >= 2
    assert client.post("/api/enhance", json={"group_id": g["id"], "model": "none", "scale": 1}).status_code == 200
    st = _wait(client, "/api/enhance/status")
    assert st["error"] is None, st
    res = st["result"]
    assert sum(1 for s in res["params"]["sources"] if s["role"] == "merged") >= 1
    r = client.get(res["image_url"])
    assert r.status_code == 200
    img = Image.open(io.BytesIO(r.content))
    assert img.size == (est["output"]["width"], est["output"]["height"])
    assert img.getexif().get(0x0131) == "Photo Manager Enhance"
    assert _snapshot(photos) == before                           # originals untouched
    # save a copy: never inside the library
    assert client.post(f"/api/generated/{res['id']}/save", json={"dest": str(photos)}).status_code == 400
    assert client.delete(f"/api/generated/{res['id']}").status_code == 200
    assert client.get(f"/api/generated/{res['id']}").status_code == 404


# ---------------------------------------------------------------- similarity scale
def test_similarity_scale_presets():
    from app import config

    assert config.similarity_preset(5) == {k: v for k, v in config.DEFAULT_SETTINGS.items()}
    keys = list(config.DEFAULT_SETTINGS)
    levels = [config.similarity_preset(i) for i in range(1, 11)]
    for k in keys:
        vals = [p[k] for p in levels]
        loosening = vals == sorted(vals) or vals == sorted(vals, reverse=True)
        assert loosening and vals[0] != vals[-1], k                       # monotonic, and it does something
    for p in levels:                                                       # internal ordering stays valid
        assert p["clip_candidate"] <= p["clip_near"] <= p["clip_similar"] <= p["clip_duplicate"]
        assert p["phash_duplicate"] <= p["phash_candidate"] and p["dhash_duplicate"] <= p["phash_candidate"] + 2
    assert config.similarity_preset(1)["clip_similar"] > config.similarity_preset(10)["clip_similar"]
    assert config.similarity_preset(1)["phash_duplicate"] < config.similarity_preset(10)["phash_duplicate"]
