import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, Person } from "../api";
import { num } from "../format";

function PersonCard({ p, others }: { p: Person; others: Person[] }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState<string | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ["people"] });
  const rename = useMutation({ mutationFn: (name: string) => api.patchPerson(p.id, { name }), onSuccess: done, onError: (e: Error) => setErr(e.message) });
  const hide = useMutation({ mutationFn: () => api.patchPerson(p.id, { hidden: !p.hidden }), onSuccess: done });
  const merge = useMutation({ mutationFn: (into: number) => api.mergePerson(p.id, into), onSuccess: done, onError: (e: Error) => setErr(e.message) });

  return (
    <div className="card person-card" style={p.hidden ? { opacity: 0.55 } : undefined}>
      <button className="person-face" onClick={() => nav(`/library?person_id=${p.id}`)} title="Show this person's photos">
        {p.cover_face_id && <img src={`/api/faces/${p.cover_face_id}/crop`} alt="" loading="lazy" />}
      </button>
      <input
        key={p.name ?? ""}
        className="person-name"
        placeholder={`Person ${p.id} — add name`}
        defaultValue={p.name ?? ""}
        onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
        onBlur={(e) => e.currentTarget.value.trim() !== (p.name ?? "") && rename.mutate(e.currentTarget.value)}
        aria-label="Person name"
      />
      <div className="sub" style={{ fontSize: 12 }}>
        {num(p.photos)} photos · {num(p.faces)} faces
      </div>
      {err && <div className="callout error" style={{ fontSize: 12 }}>{err}</div>}
      <div className="row" style={{ gap: 6 }}>
        <select
          value=""
          onChange={(e) => e.target.value && confirm("Merge this person into the selected one?") && merge.mutate(Number(e.target.value))}
          aria-label="Merge into"
          style={{ flex: 1, minWidth: 0 }}
        >
          <option value="">Merge into…</option>
          {others.map((o) => <option key={o.id} value={o.id}>{o.name ?? `Person ${o.id}`}</option>)}
        </select>
        <button className="small ghost" onClick={() => hide.mutate()} title={p.hidden ? "Show again" : "Hide (not a person you care about)"}>
          {p.hidden ? "Unhide" : "Hide"}
        </button>
      </div>
    </div>
  );
}

export default function People() {
  const qc = useQueryClient();
  const [showHidden, setShowHidden] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["people", showHidden], queryFn: () => api.people(showHidden) });
  const [thr, setThr] = useState<number | null>(null);
  const regroup = useMutation({ mutationFn: (t: number) => api.recluster(t), onSuccess: () => qc.invalidateQueries({ queryKey: ["people"] }) });
  const threshold = thr ?? data?.threshold ?? 0.5;
  const people = data?.people ?? [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p className="sub">
            {isLoading ? "Loading…" : `${num(people.length)} people · ${num(data?.faces)} faces in ${num(data?.scanned)} scanned photos`}
          </p>
        </div>
      </div>
      {!isLoading && !data?.scanned && (
        <div className="callout">No faces yet. Run a scan from “Folders &amp; scan” with the “Faces” option enabled.</div>
      )}
      {!!data?.scanned && (
        <div className="row" style={{ marginBottom: 16, flexWrap: "wrap" }}>
          <label className="row" style={{ fontSize: 13 }} title="Higher = stricter: fewer wrong matches, but the same person may be split into several groups (merge them by hand)">
            Match strictness
            <input type="range" min={0.3} max={0.8} step={0.05} value={threshold} onChange={(e) => setThr(Number(e.target.value))} />
            {threshold.toFixed(2)}
          </label>
          <button className="small" disabled={regroup.isPending} onClick={() => regroup.mutate(threshold)}>
            {regroup.isPending ? "Regrouping…" : "Regroup"}
          </button>
          <span className="sub" style={{ fontSize: 12 }}>Names and merges you made are kept.</span>
          <div className="grow" />
          <label className="row" style={{ fontSize: 13 }}>
            <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> Show hidden
          </label>
        </div>
      )}
      <div className="people-grid">
        {people.map((p) => <PersonCard key={p.id} p={p} others={people.filter((o) => o.id !== p.id)} />)}
      </div>
      {!!data?.unassigned && <p className="sub" style={{ marginTop: 16 }}>{num(data.unassigned)} faces appear only once and are not grouped.</p>}
    </div>
  );
}
