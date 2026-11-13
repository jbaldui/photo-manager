"""Face clustering + People API, using synthetic embeddings (no model download needed)."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db, faces


def _unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture()
def client():
    db.init()
    with db.tx() as c:
        for t in ("faces", "people", "faces_done"):
            c.execute(f"DELETE FROM {t}")
    from app.main import app

    return TestClient(app)


def _insert(emb, sha):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO faces(sha256, x0, y0, x1, y1, score, emb) VALUES(?,?,?,?,?,?,?)", (sha, 0.1, 0.1, 0.4, 0.4, 0.9, emb.tobytes())
        ).lastrowid


def _two_people():
    rng = np.random.default_rng(0)
    centers = [_unit(rng.normal(size=faces.EMB_DIM)) for _ in range(2)]
    ids = [[_insert(_unit(c + 0.15 * _unit(rng.normal(size=faces.EMB_DIM))), f"s{k}_{i}") for i in range(6)] for k, c in enumerate(centers)]
    return centers, ids


def test_cluster_separates_people(client):
    _, ids = _two_people()
    res = faces.cluster()
    assert res["people"] == 2
    pid = lambda f: db.get().execute("SELECT person_id FROM faces WHERE id=?", (f,)).fetchone()[0]
    assert len({pid(f) for f in ids[0]}) == 1 and len({pid(f) for f in ids[1]}) == 1
    assert pid(ids[0][0]) != pid(ids[1][0])


def test_singleton_stays_unassigned(client):
    _two_people()
    lone = _insert(_unit(np.random.default_rng(9).normal(size=faces.EMB_DIM)), "lone")
    faces.cluster()
    assert db.get().execute("SELECT person_id FROM faces WHERE id=?", (lone,)).fetchone()[0] is None


def test_rename_merge_and_filter(client):
    _, ids = _two_people()
    faces.cluster()
    people = client.get("/api/people").json()["people"]
    assert [p["faces"] for p in people] == [6, 6]
    a, b = people[0]["id"], people[1]["id"]
    assert client.patch(f"/api/people/{a}", json={"name": "Ana"}).status_code == 200
    assert client.patch(f"/api/people/{b}", json={"name": "ana"}).status_code == 409   # duplicate name
    assert client.post(f"/api/people/{b}/merge", json={"into": a}).json()["moved"] == 6
    people = client.get("/api/people").json()["people"]
    assert len(people) == 1 and people[0]["name"] == "Ana" and people[0]["faces"] == 12
    # merged faces are pinned: re-clustering must not split them again
    faces.cluster()
    assert client.get("/api/people").json()["people"][0]["faces"] == 12


def test_manual_assignment_survives_recluster(client):
    _, ids = _two_people()
    faces.cluster()
    a = client.get("/api/people").json()["people"][0]["id"]
    stray = ids[1][0] if db.get().execute("SELECT person_id FROM faces WHERE id=?", (ids[1][0],)).fetchone()[0] != a else ids[0][0]
    assert client.post(f"/api/faces/{stray}/assign", json={"person_id": a}).status_code == 200
    faces.cluster()
    assert db.get().execute("SELECT person_id FROM faces WHERE id=?", (stray,)).fetchone()[0] == a


def test_new_faces_join_named_person(client):
    centers, ids = _two_people()
    faces.cluster()
    p = db.get().execute("SELECT person_id FROM faces WHERE id=?", (ids[0][0],)).fetchone()[0]
    client.patch(f"/api/people/{p}", json={"name": "Bea"})
    new = _insert(_unit(centers[0] + 0.1 * _unit(np.random.default_rng(5).normal(size=faces.EMB_DIM))), "new")
    faces.cluster()
    assert db.get().execute("SELECT person_id FROM faces WHERE id=?", (new,)).fetchone()[0] == p
