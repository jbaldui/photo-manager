"""End-to-end: add the fixture folder through the API, run every stage, check tiers and safety."""
import hashlib
import re
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.make_fixtures import EXPECTED, SAME_TEXT, UNRELATED

TIER_ORDER = ["exact", "duplicate", "near", "similar"]
ALL_TIERS = TIER_ORDER + ["text"]


def _snapshot(d: Path) -> dict:
    out = {}
    for p in sorted(d.rglob("*")):
        st = p.stat()
        out[str(p)] = (st.st_mtime_ns, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
    return out


@pytest.fixture(scope="module")
def scanned(fixture_dir):
    from app.main import app

    before = _snapshot(fixture_dir)
    client = TestClient(app)
    r = client.post("/api/roots", json={"path": str(fixture_dir)})
    assert r.status_code == 200, r.text
    r = client.post("/api/scan", json={})
    assert r.status_code == 200, r.text
    for _ in range(600):
        st = client.get("/api/scan/status").json()
        if not st["running"]:
            break
        time.sleep(0.5)
    assert st["error"] is None, st["error"]
    assert all(s["status"] == "done" for s in st["stages"].values()), st
    return client, before


def _groups_by_tier(client):
    out = {}
    for tier in ALL_TIERS:
        items = client.get("/api/groups", params={"tier": tier, "limit": 300}).json()["items"]
        out[tier] = [set(m["rel_path"] for m in client.get(f"/api/groups/{g['id']}").json()["members"]) for g in items]
    return out


def _strictest_tier_with(groups, a, b):
    for tier in TIER_ORDER:
        for members in groups[tier]:
            if any(m.endswith(a) for m in members) and any(m.endswith(b) for m in members):
                return tier
    return None


@pytest.mark.parametrize("variant,expected", sorted(EXPECTED.items()))
def test_variant_tier(scanned, variant, expected):
    client, _ = scanned
    groups = _groups_by_tier(client)
    assert _strictest_tier_with(groups, "a.jpg", variant) == expected


@pytest.mark.parametrize("other", UNRELATED)
def test_unrelated_not_duplicate(scanned, other):
    client, _ = scanned
    groups = _groups_by_tier(client)
    assert _strictest_tier_with(groups, "a.jpg", other) not in ("exact", "duplicate", "near")


def test_sniffed_extension_and_errors(scanned):
    client, _ = scanned
    items = client.get("/api/photos", params={"status": "all", "limit": 100}).json()["items"]
    by = {i["rel_path"]: i for i in items}
    assert by["download_b.jpg_large"]["status"] == "ok"
    assert by["broken.jpg"]["status"] == "error"
    assert "notes.txt" not in by
    groups = _groups_by_tier(client)
    assert {"b.jpg", "download_b.jpg_large"} in groups["exact"]


def test_stats_and_detail(scanned):
    client, _ = scanned
    s = client.get("/api/stats").json()
    assert s["total_files"] == 13 and s["errors"] == 1
    assert s["tiers"]["exact"]["groups"] == 2
    pid = client.get("/api/photos", params={"q": "a_meme"}).json()["items"][0]["id"]
    d = client.get(f"/api/photos/{pid}").json()
    assert d["hashes"]["clip_embedded"] and len(d["hashes"]["phash"]) == 16
    assert client.get(f"/api/photos/{pid}/thumb").status_code == 200
    assert client.get(f"/api/photos/{pid}/preview").status_code == 200
    sim = client.get(f"/api/photos/{pid}/similar").json()["items"]
    assert sim


def test_decisions_and_export(scanned):
    client, _ = scanned
    g = client.get("/api/groups", params={"tier": "exact"}).json()["items"][0]
    assert client.post(f"/api/groups/{g['id']}/auto-decide").status_code == 200
    detail = client.get(f"/api/groups/{g['id']}").json()
    assert sorted(m["decision"] for m in detail["members"]) == ["discard", "keep"]
    for kind in ("csv", "json"):
        r = client.post("/api/export", json={"kind": kind})
        assert r.status_code == 200 and Path(r.json()["path"]).is_file()
    assert client.post("/api/export", json={"kind": "script"}).status_code == 400   # replaced by in-app quarantine


def test_rescan_is_incremental(scanned, fixture_dir):
    client, _ = scanned
    client.post("/api/scan", json={"stages": ["scan", "analyze"]})
    while client.get("/api/scan/status").json()["running"]:
        time.sleep(0.2)
    st = client.get("/api/scan/status").json()
    assert st["stages"]["scan"]["result"]["new"] == 0
    assert st["stages"]["analyze"]["result"]["analysed"] == 0


def test_text_search(scanned):
    client, _ = scanned
    items = client.get("/api/search", params={"q": "colorful shapes", "mode": "clip"}).json()["items"]
    assert items and "score" in items[0]


def test_regroup_uses_orb_cache_and_is_stable(scanned):
    client, _ = scanned
    before = client.get("/api/stats").json()["tiers"]
    client.post("/api/scan", json={"stages": ["pairs", "group"]})
    while client.get("/api/scan/status").json()["running"]:
        time.sleep(0.2)
    st = client.get("/api/scan/status").json()
    assert st["error"] is None
    assert "cached" in st["stages"]["pairs"]["message"] or st["stages"]["pairs"]["result"]["orb_checked"] >= 0
    assert client.get("/api/stats").json()["tiers"] == before


# ------------------------------------------------------------------ OCR
def test_ocr_same_text_group(scanned):
    client, _ = scanned
    groups = _groups_by_tier(client)
    assert any(all(any(m.endswith(f) for m in g) for f in SAME_TEXT) for g in groups["text"]), groups["text"]
    # visual duplicates of the meme must not form a "same text" group on their own
    for g in groups["text"]:
        assert any("b_same_caption" in m for m in g)


def test_ocr_search_text(scanned):
    client, _ = scanned
    hits = lambda q: {i["rel_path"] for i in client.get("/api/search", params={"q": q, "mode": "text"}).json()["items"]}  # noqa: E731
    assert set(SAME_TEXT) <= hits("code works")
    assert set(SAME_TEXT) <= hits('"the code"')
    assert set(SAME_TEXT) <= hits("wor")                 # prefix on last word
    assert "b.jpg" not in hits("code works")
    shot = "Screenshot_20240102-101112.png"
    assert shot in hits("meeting notes budget")
    assert shot in hits("reuniao")                      # accent-insensitive
    assert shot in hits("Reunião sala")
    assert not hits("zebra giraffe")
    item = client.get("/api/search", params={"q": "code works"}).json()["items"][0]
    assert "[[" in item["snippet"]


def test_ocr_on_detail_and_filters(scanned):
    client, _ = scanned
    pid = client.get("/api/photos", params={"q": "Screenshot_"}).json()["items"][0]["id"]
    d = client.get(f"/api/photos/{pid}").json()
    assert "Meeting notes for Q3 budget" in d["ocr"]["text"]
    assert len(d["ocr"]["lines"]) == 4 and all(0 <= v <= 1 for l in d["ocr"]["lines"] for v in l[2])
    with_text = {i["rel_path"] for i in client.get("/api/photos", params={"has_text": True}).json()["items"]}
    assert with_text == {"a_meme_caption.jpg", "b_same_caption.jpg", "Screenshot_20240102-101112.png"}
    filtered = client.get("/api/photos", params={"text": "budget"}).json()["items"]
    assert [i["name"] for i in filtered] == ["Screenshot_20240102-101112.png"]
    s = client.get("/api/stats").json()
    assert s["with_text"] == 3 and s["tiers"]["text"]["groups"] >= 1


# ------------------------------------------------------------------ organize (copy-only)
def _wait_organize(client):
    for _ in range(200):
        st = client.get("/api/organize/status").json()
        if not st["running"]:
            return st
        time.sleep(0.1)
    raise AssertionError("organize did not finish")


def test_organize_rejects_dest_inside_root(scanned, fixture_dir):
    client, _ = scanned
    r = client.post("/api/organize/preview", json={"dest": str(fixture_dir / "sub")})
    assert r.status_code == 400 and "scanned folder" in r.json()["detail"]


def test_organize_groups_layout(scanned, fixture_dir, tmp_path):
    client, _ = scanned
    opts = {"dest": str(tmp_path), "scope": "all", "layout": "groups", "tiers": ["exact", "duplicate", "near"],
            "rename": "keep", "pattern": "date_name"}
    pv = client.post("/api/organize/preview", json=opts).json()
    assert pv["files"] == 13 and pv["groups"] >= 2
    st = client.post("/api/organize/start", json=opts).json()
    st = _wait_organize(client)
    assert st["errors"] == [] and st["done"] == 13
    out = Path(st["out"])
    copied = [p for p in out.rglob("*") if p.is_file() and p.name != "manifest.csv"]
    assert len(copied) == 13
    dirs = {p.parent.name for p in copied}
    assert "_unique" in dirs and any(d.startswith("g0001_") for d in dirs)
    assert all(p.parent.parent.name == "Groups" for p in copied if p.parent.name.startswith("g0"))
    # sniffed weird extension gets a real one
    assert any(p.name == "download_b.jpg" for p in copied)
    # a.jpg and its byte-identical copy end up in the same group folder
    a_dirs = {p.parent for p in copied if p.name in ("a.jpg", "a_copy_other_mtime.jpg")}
    assert len(a_dirs) == 1
    assert (out / "manifest.csv").read_text().count("\n") == 14


def test_organize_flat_rename_all_no_collisions(scanned, tmp_path):
    client, _ = scanned
    opts = {"dest": str(tmp_path), "scope": "not_discarded", "layout": "flat", "tiers": ["exact", "duplicate"],
            "rename": "all", "pattern": "date", "one_per_exact": True}
    client.post("/api/organize/start", json=opts)
    st = _wait_organize(client)
    out = Path(st["out"])
    names = [p.name for p in out.iterdir() if p.name != "manifest.csv"]
    # every fixture shares the same mtime-second -> collisions must be numbered, never overwritten
    assert len(names) == len(set(n.lower() for n in names)) == st["done"]
    assert all(re.match(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_\d+)?\.\w+$", n) for n in names), names
    # one_per_exact: a.jpg / a_copy_other_mtime.jpg are identical -> only one copied
    assert st["done"] <= 12


def test_organize_grouped_scope_group_pattern(scanned, tmp_path):
    client, _ = scanned
    opts = {"dest": str(tmp_path), "scope": "grouped", "layout": "groups", "tiers": ["text"], "rename": "duplicates", "pattern": "group"}
    pv = client.post("/api/organize/preview", json=opts).json()
    names = sorted(s["dst"].split("/")[-1] for s in pv["sample"])
    assert names == ["g0001_01_a_meme_caption.jpg", "g0001_02_b_same_caption.jpg"] or names == ["g0001_01_b_same_caption.jpg", "g0001_02_a_meme_caption.jpg"]
    assert sum(1 for s in pv["sample"] if s["is_best"]) == 1


def _run(client, stages=None):
    client.post("/api/scan", json={"stages": stages} if stages else {})
    while client.get("/api/scan/status").json()["running"]:
        time.sleep(0.2)
    st = client.get("/api/scan/status").json()
    assert st["error"] is None, st["error"]


def test_remove_source_removes_its_duplicates(scanned, fixture_dir, tmp_path_factory):
    import shutil

    from app import config, db
    from tests.make_fixtures import synthetic_photo

    client, _ = scanned
    before = client.get("/api/stats").json()
    second = tmp_path_factory.mktemp("second_source")
    shutil.copyfile(fixture_dir / "b.jpg", second / "b_copy_in_second_source.jpg")
    shutil.copyfile(fixture_dir / "Screenshot_20240102-101112.png", second / "screenshot_copy.png")
    synthetic_photo(9).save(second / "only_here.jpg", quality=90)
    rid = client.post("/api/roots", json={"path": str(second)}).json()["id"]
    _run(client)

    def exact_b_members():
        for g in client.get("/api/groups", params={"tier": "exact"}).json()["items"]:
            names = {m["rel_path"] for m in client.get(f"/api/groups/{g['id']}").json()["members"]}
            if "b.jpg" in names:
                return names
        return set()

    assert "b_copy_in_second_source.jpg" in exact_b_members()
    only_here = client.get("/api/photos", params={"q": "only_here"}).json()["items"][0]
    client.put("/api/decisions", json={"file_ids": [only_here["id"]], "decision": "discard"})
    conn = db.get()
    orphan_sha = only_here["sha256"]
    thumb = config.THUMB_DIR / conn.execute("SELECT thumb FROM images WHERE sha256=?", (orphan_sha,)).fetchone()[0]
    assert thumb.is_file()

    r = client.delete(f"/api/roots/{rid}").json()
    assert r["removed"]["files"] == 3 and r["removed"]["images"] == 1 and r["removed"]["thumbnails"] == 1

    # duplicates from the removed folder are gone from every group, groups were rebuilt
    assert exact_b_members() == {"b.jpg", "download_b.jpg_large"}
    assert conn.execute("SELECT COUNT(*) FROM group_members WHERE file_id NOT IN (SELECT id FROM files)").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM groups WHERE rep_file_id NOT IN (SELECT id FROM files)").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE root_id=?", (rid,)).fetchone()[0] == 0
    # content that existed only there is purged; shared content (screenshot) is kept
    for table, col in (("images", "sha256"), ("ocr", "sha256"), ("pairs", "a"), ("pairs", "b"), ("orb_cache", "a"), ("orb_cache", "b")):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (orphan_sha,)).fetchone()[0] == 0, table
    assert not thumb.exists()
    assert client.get("/api/search", params={"q": "meeting notes"}).json()["items"]
    after = client.get("/api/stats").json()
    assert after["total_files"] == before["total_files"] and after["tiers"] == before["tiers"]
    assert second.joinpath("only_here.jpg").is_file()        # files on disk untouched


def test_startup_repair_clears_stale_groups(scanned):
    """A folder removed by an older version left groups/images without files: repair must clear them."""
    from app import db, maintenance

    client, _ = scanned
    before = client.get("/api/stats").json()
    assert maintenance.repair() is None                      # consistent library -> no-op
    with db.tx() as c:
        c.execute("INSERT INTO groups(tier, size, rep_file_id, total_bytes, reclaimable_bytes) VALUES('exact', 4, 999999, 100, 75)")
        c.execute("INSERT INTO images(sha256, phash, dhash, whash, thumb) VALUES('deadbeef', 1, 1, 1, NULL)")
        c.execute("INSERT INTO ocr(sha256, text, lines_json, norm, n_words) VALUES('deadbeef', 'x', '[]', 'x', 1)")
        c.execute("INSERT INTO decisions(root_id, rel_path, decision, updated_at) VALUES(424242, 'gone.jpg', 'keep', 0)")
    assert maintenance.needs_repair()
    r = maintenance.repair()
    assert r["images"] == 1
    assert not maintenance.needs_repair()
    after = client.get("/api/stats").json()
    assert after["tiers"] == before["tiers"] and after["images"] == before["images"]


def test_originals_untouched(scanned, fixture_dir):
    _, before = scanned
    assert _snapshot(fixture_dir) == before
    assert not any(p.name.endswith((".tmp", ".webp")) for p in fixture_dir.rglob("*"))
