"""Stage 5: turn scored pairs into groups at four strictness tiers.

exact      byte-identical files (same SHA-256)
duplicate  same picture re-encoded / resized / format-converted / rotated  (union-find)
near       edited versions: crops, captions, filters, watermarks            (star clustering)
similar    same scene or burst shots, not verified geometrically            (star clustering)
text       different images carrying the same text (OCR word sets)          (star clustering)

Star clustering means every member is directly similar to the group centre, so A~B~C chains
never pull unrelated images together in the looser tiers.
"""
from __future__ import annotations

from collections import defaultdict

from . import db

TIERS = ["exact", "duplicate", "near", "similar", "text"]
_ALLOWED = {"duplicate": {"duplicate"}, "near": {"duplicate", "near"}, "similar": {"duplicate", "near", "similar"}}


def _rep_key(f) -> tuple:
    """Higher is better: resolution, real capture date, metadata, oldest copy (likely the original), size."""
    return (
        (f["width"] or 0) * (f["height"] or 0),
        1 if (f["date_source"] or "").startswith("exif") else 0,
        f["has_exif"] or 0,
        -(f["mtime"] or 0),
        f["size"] or 0,
    )


def _union_find(nodes, edges):
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = defaultdict(list)
    for n in nodes:
        comps[find(n)].append(n)
    return [c for c in comps.values() if len(c) > 1]


def _star(edges):
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    assigned: set = set()
    groups = []
    for center in sorted(adj, key=lambda n: -len(adj[n])):
        if center in assigned:
            continue
        members = [center] + [m for m in adj[center] if m not in assigned]
        if len(members) > 1:
            groups.append(members)
            assigned.update(members)
    return groups


def build_groups(ctx) -> dict:
    conn = db.get()
    files = conn.execute(
        "SELECT id, sha256, size, mtime, width, height, has_exif, date_source FROM files WHERE status='ok' AND sha256 IS NOT NULL"
    ).fetchall()
    by_sha: dict[str, list] = defaultdict(list)
    for f in files:
        by_sha[f["sha256"]].append(f)
    pairs = conn.execute("SELECT a, b, tier, text_sim FROM pairs").fetchall()

    out: list[tuple[str, list]] = []   # (tier, [file rows])
    # exact
    for sha, fl in by_sha.items():
        if len(fl) > 1:
            out.append(("exact", fl))

    # Each looser tier only shows groups that add something over the stricter one.
    prev_membership: dict[str, int] = {}
    for tier in ("duplicate", "near", "similar"):
        edges = [(p["a"], p["b"]) for p in pairs if p["tier"] in _ALLOWED[tier] and p["a"] in by_sha and p["b"] in by_sha]
        nodes = {x for e in edges for x in e}
        comps = _union_find(nodes, edges) if tier == "duplicate" else _star(edges)
        membership: dict[str, int] = {}
        for gi, shas in enumerate(comps):
            for s in shas:
                membership[s] = gi
            if tier != "duplicate":
                prev_ids = {prev_membership.get(s, f"solo:{s}") for s in shas}
                if len(prev_ids) < 2:
                    continue   # identical to a stricter group
            out.append((tier, [f for s in shas for f in by_sha[s]]))
        prev_membership = membership

    # Same text: skip groups that are just one visual duplicate/edit cluster seen again.
    visual = _union_find(
        {x for p in pairs if p["tier"] in ("duplicate", "near") for x in (p["a"], p["b"])},
        [(p["a"], p["b"]) for p in pairs if p["tier"] in ("duplicate", "near")],
    )
    vis_of = {s: i for i, comp in enumerate(visual) for s in comp}
    text_edges = [(p["a"], p["b"]) for p in pairs if p["text_sim"] is not None and p["a"] in by_sha and p["b"] in by_sha]
    for shas in _star(text_edges):
        if len({vis_of.get(s, f"solo:{s}") for s in shas}) < 2:
            continue
        out.append(("text", [f for s in shas for f in by_sha[s]]))

    with db.tx() as c:
        c.execute("DELETE FROM group_members")
        c.execute("DELETE FROM groups")
        for tier, fl in out:
            fl = sorted(fl, key=_rep_key, reverse=True)
            rep = fl[0]
            total = sum(f["size"] or 0 for f in fl)
            cur = c.execute(
                "INSERT INTO groups(tier, size, rep_file_id, total_bytes, reclaimable_bytes) VALUES(?,?,?,?,?)",
                (tier, len(fl), rep["id"], total, total - (rep["size"] or 0)),
            )
            gid = cur.lastrowid
            c.executemany("INSERT INTO group_members(group_id, file_id) VALUES(?,?)", [(gid, f["id"]) for f in fl])
    counts = {t: sum(1 for x, _ in out if x == t) for t in TIERS}
    ctx.update(1, 1, "Groups: " + ", ".join(f"{v:,} {k}" for k, v in counts.items()))
    return counts
