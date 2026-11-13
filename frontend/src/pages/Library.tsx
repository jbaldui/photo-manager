import { forwardRef, useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { VirtuosoGrid } from "react-virtuoso";
import { api } from "../api";
import { DecisionBadge, Thumb } from "../components/ui";
import { DATE_SOURCE_LABEL, num, TIER_INFO } from "../format";

const PAGE = 300;
const FILTER_KEYS = ["q", "text", "has_text", "person_id", "ext", "year", "camera", "source", "tier", "decision", "has_date", "status", "sort", "order"] as const;

const GridList = forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>((props, ref) => (
  <div ref={ref} {...props} className="grid-list" />
));

export default function Library() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const params = useMemo(() => {
    const p: Record<string, string> = {};
    FILTER_KEYS.forEach((k) => {
      const v = sp.get(k);
      if (v) p[k] = v;
    });
    return p;
  }, [sp]);
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const { data: people } = useQuery({ queryKey: ["people"], queryFn: () => api.people() });

  const q = useInfiniteQuery({
    queryKey: ["photos", params],
    queryFn: ({ pageParam }) => api.photos({ ...params, offset: pageParam, limit: PAGE }),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total ? last.offset + last.items.length : undefined),
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const total = q.data?.pages[0]?.total ?? 0;
  const activeFilters = Object.keys(params).filter((k) => !["sort", "order"].includes(k));

  return (
    <div className="page" style={{ height: "100%", display: "flex", flexDirection: "column", paddingBottom: 0 }}>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div>
          <h1>Library</h1>
          <p className="sub">{q.isLoading ? "Loading…" : `${num(total)} photos`}</p>
        </div>
      </div>
      <div className="filters">
        <input key={params.q ?? ""} placeholder="Filter by file name or path…" defaultValue={params.q ?? ""} onKeyDown={(e) => e.key === "Enter" && set("q", e.currentTarget.value)} onBlur={(e) => set("q", e.currentTarget.value)} />
        <input
          key={`t-${params.text ?? ""}`}
          placeholder="Text in image…"
          defaultValue={params.text ?? ""}
          onKeyDown={(e) => e.key === "Enter" && set("text", e.currentTarget.value)}
          onBlur={(e) => set("text", e.currentTarget.value)}
          title="Only photos whose OCR text contains these words"
        />
        <select value={params.has_text ?? ""} onChange={(e) => set("has_text", e.target.value)} aria-label="Has text">
          <option value="">With or without text</option>
          <option value="true">Contains text</option>
          <option value="false">No text</option>
        </select>
        {!!people?.people.length && (
          <select value={params.person_id ?? ""} onChange={(e) => set("person_id", e.target.value)} aria-label="Person">
            <option value="">Anyone</option>
            {people.people.map((p) => <option key={p.id} value={p.id}>{p.name ?? `Person ${p.id}`} ({num(p.photos)})</option>)}
          </select>
        )}
        <select value={params.ext ?? ""} onChange={(e) => set("ext", e.target.value)} aria-label="Format">
          <option value="">All formats</option>
          {stats?.formats.map((f) => <option key={f.key} value={f.key}>{f.key.toUpperCase()} ({num(f.count)})</option>)}
        </select>
        <select value={params.year ?? ""} onChange={(e) => set("year", e.target.value)} aria-label="Year">
          <option value="">Any year</option>
          {stats?.years.map((f) => <option key={f.key} value={f.key}>{f.key === "unknown" ? "No date" : f.key} ({num(f.count)})</option>)}
        </select>
        <select value={params.source ?? ""} onChange={(e) => set("source", e.target.value)} aria-label="Source">
          <option value="">Any source</option>
          {stats?.sources.map((f) => <option key={f.key} value={f.key}>{f.key} ({num(f.count)})</option>)}
        </select>
        <select value={params.camera ?? ""} onChange={(e) => set("camera", e.target.value)} aria-label="Camera">
          <option value="">Any camera</option>
          {stats?.cameras.map((f) => <option key={f.key} value={f.model}>{f.key} ({num(f.count)})</option>)}
        </select>
        <select value={params.tier ?? ""} onChange={(e) => set("tier", e.target.value)} aria-label="In group">
          <option value="">Any grouping</option>
          {Object.entries(TIER_INFO).map(([k, v]) => <option key={k} value={k}>In: {v.label}</option>)}
        </select>
        <select value={params.decision ?? ""} onChange={(e) => set("decision", e.target.value)} aria-label="Decision">
          <option value="">Any decision</option>
          <option value="keep">Marked keep</option>
          <option value="discard">Marked discard</option>
          <option value="undecided">Undecided</option>
        </select>
        <select value={params.sort ?? "date_taken"} onChange={(e) => set("sort", e.target.value)} aria-label="Sort by">
          <option value="date_taken">Sort: date taken</option>
          <option value="mtime">Sort: last modified</option>
          <option value="size">Sort: file size</option>
          <option value="resolution">Sort: resolution</option>
          <option value="name">Sort: name</option>
          <option value="path">Sort: folder</option>
        </select>
        <button className="small" onClick={() => set("order", params.order === "asc" ? "desc" : "asc")} title="Toggle order">
          {params.order === "asc" ? "↑ asc" : "↓ desc"}
        </button>
        {activeFilters.length > 0 && (
          <button className="small ghost" onClick={() => setSp(new URLSearchParams(), { replace: true })}>Clear filters</button>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 300 }}>
        {!q.isLoading && !items.length ? (
          <div className="card empty"><h2>No photos match</h2><p>Try removing a filter.</p></div>
        ) : (
          <VirtuosoGrid
            style={{ height: "100%" }}
            totalCount={items.length}
            components={{ List: GridList }}
            endReached={() => q.hasNextPage && !q.isFetchingNextPage && q.fetchNextPage()}
            overscan={600}
            itemContent={(i) => {
              const p = items[i];
              const date = p.date_taken ? p.date_taken.slice(0, 10) : DATE_SOURCE_LABEL.none;
              return (
                <Thumb
                  photo={p}
                  onClick={() => nav(`/photo/${p.id}`)}
                  badge={<DecisionBadge d={p.decision} />}
                  caption={`${date} · ${p.name}`}
                />
              );
            }}
          />
        )}
      </div>
    </div>
  );
}
