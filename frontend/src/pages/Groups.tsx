import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, Tier } from "../api";
import RemoveModal from "../components/RemoveModal";
import { Thumb } from "../components/ui";
import { bytes, num, TIER_INFO } from "../format";

const TIERS: Tier[] = ["exact", "duplicate", "near", "similar", "text"];

export default function Groups() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const tier = (sp.get("tier") as Tier) || "exact";
  const sort = sp.get("sort") || "reclaimable";
  const undecided = sp.get("undecided") === "1";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const [moving, setMoving] = useState(false);
  const discards = stats?.decisions.find((d) => d.key === "discard")?.count ?? 0;

  const q = useInfiniteQuery({
    queryKey: ["groups", tier, sort, undecided],
    queryFn: ({ pageParam }) => api.groups({ tier, sort, offset: pageParam, limit: 60, undecided: undecided || undefined }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => {
      const n = all.reduce((s, p) => s + p.items.length, 0);
      return n < last.total ? n : undefined;
    },
  });
  const groups = q.data?.pages.flatMap((p) => p.items) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;

  const bulk = useMutation({
    mutationFn: () => api.autoDecideTier(tier),
    onSuccess: (r) => {
      qc.invalidateQueries();
      if (r.groups_decided && confirm(`Decided ${r.groups_decided} groups. Nothing has been moved yet.\n\nReview the discarded photos and move them to quarantine now?`))
        setMoving(true);
    },
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Duplicates &amp; similar photos</h1>
          <p className="sub">{TIER_INFO[tier].help}</p>
        </div>
      </div>
      <div className="row" style={{ marginBottom: 16 }}>
        <div className="seg" role="tablist">
          {TIERS.map((t) => (
            <button key={t} role="tab" aria-selected={t === tier} className={t === tier ? "active" : ""} onClick={() => set("tier", t)}>
              {TIER_INFO[t].label} <span className="badge">{num(stats?.tiers[t]?.groups ?? 0)}</span>
            </button>
          ))}
        </div>
        <div className="grow" />
        <label className="row" style={{ fontSize: 13 }}>
          <input type="checkbox" checked={undecided} onChange={(e) => set("undecided", e.target.checked ? "1" : "")} /> Only undecided
        </label>
        <select value={sort} onChange={(e) => set("sort", e.target.value)} aria-label="Sort groups">
          <option value="reclaimable">Most space to free</option>
          <option value="size">Most files</option>
          <option value="total">Largest total size</option>
          <option value="id">Default</option>
        </select>
        {(tier === "exact" || tier === "duplicate") && total > 0 && (
          <button
            onClick={() =>
              confirm(
                `Mark the best copy as KEEP and the others as DISCARD in every undecided ${TIER_INFO[tier].label.toLowerCase()} group?\n\nThis only records decisions; no file is moved or deleted.`,
              ) && bulk.mutate()
            }
            disabled={bulk.isPending}
          >
            Auto-decide all
          </button>
        )}
        {discards > 0 && (
          <button className="primary" onClick={() => setMoving(true)} title="Move every photo marked discard out of your library (undoable)">
            Move {num(discards)} discarded to quarantine…
          </button>
        )}
      </div>

      {!q.isLoading && !groups.length && (
        <div className="card empty">
          <h2>No {TIER_INFO[tier].label.toLowerCase()} found</h2>
          <p>
            {stats?.removed && !stats?.tiers[tier]
              ? `No ${TIER_INFO[tier].label.toLowerCase()} left: ${num(stats.removed)} photo(s) are in quarantine.`
              : stats?.total_files
                ? "Nothing in this tier — or the grouping stage hasn't run yet."
                : "Run a scan first."}
          </p>
        </div>
      )}

      <div className="grid-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
        {groups.map((g) => (
          <div key={g.id} className="card group-card" style={{ cursor: "pointer" }} onClick={() => nav(`/groups/${g.id}`)}>
            <div className="strip">
              {g.preview?.slice(0, 4).map((p, i) => (
                <Thumb key={p.id} photo={p} caption={i === 0 ? "best" : null} />
              ))}
            </div>
            <div className="row">
              <strong>{g.size} files</strong>
              <span className="sub" style={{ fontSize: 12 }}>{bytes(g.total_bytes)}</span>
              <span className="grow" />
              {tier !== "similar" && tier !== "text" && g.reclaimable_bytes > 0 && <span className="badge accent">frees {bytes(g.reclaimable_bytes)}</span>}
              {g.decided ? (
                <span className={`badge ${g.decided >= g.size ? "good" : "warn"}`}>{g.decided >= g.size ? "decided" : `${g.decided}/${g.size} decided`}</span>
              ) : null}
            </div>
            <div className="sub mono" style={{ fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {g.preview?.[0]?.rel_path}
            </div>
          </div>
        ))}
      </div>
      {moving && <RemoveModal onClose={() => setMoving(false)} />}
      {q.hasNextPage && (
        <div style={{ textAlign: "center", marginTop: 16 }}>
          <button onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>
            {q.isFetchingNextPage ? "Loading…" : `Load more (${num(total - groups.length)} left)`}
          </button>
        </div>
      )}
    </div>
  );
}
