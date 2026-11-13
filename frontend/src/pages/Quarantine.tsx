import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, RemovalBatch } from "../api";
import RemoveModal from "../components/RemoveModal";
import { bytes, epoch, num } from "../format";

const STATE_LABEL: Record<string, [string, string]> = {
  running: ["in progress", "warn"],
  done: ["in quarantine", "accent"],
  cancelled: ["stopped part-way", "warn"],
  error: ["finished with errors", "bad"],
  restored: ["restored", "good"],
  partially_restored: ["partly restored", "warn"],
};

export default function Quarantine() {
  const qc = useQueryClient();
  const [moving, setMoving] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const batches = useQuery({ queryKey: ["removal-batches"], queryFn: api.removalBatches });
  const status = useQuery({ queryKey: ["removal-status"], queryFn: api.removalStatus, refetchInterval: (q) => (q.state.data?.running ? 700 : 4000) });
  const restore = useMutation({
    mutationFn: api.restoreBatch,
    onSuccess: (s) => {
      qc.setQueryData(["removal-status"], s);
      qc.invalidateQueries({ queryKey: ["removal-status"] });
    },
    onError: (e: Error) => alert(e.message),
  });
  const discards = stats?.decisions.find((d) => d.key === "discard")?.count ?? 0;
  const st = status.data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Quarantine</h1>
          <p className="sub">
            Photos you discard are <strong>moved</strong> here, never deleted. Restore any batch with one click; once you are sure, delete the quarantine
            folder yourself.
          </p>
        </div>
        <button className="primary" disabled={!discards || st?.running} onClick={() => setMoving(true)}>
          Move {num(discards)} discarded to quarantine…
        </button>
      </div>

      <div className="tiles">
        <div className="tile"><div className="label">In quarantine</div><div className="value">{num(stats?.removed)}</div><div className="hint">{bytes(stats?.removed_bytes)}</div></div>
        <div className="tile"><div className="label">Marked discard</div><div className="value">{num(discards)}</div><div className="hint">still in your library</div></div>
      </div>

      {st?.running && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2>{st.mode === "restore" ? "Restoring…" : "Moving to quarantine…"}</h2>
          <div className="meter"><div style={{ width: `${st.total ? (100 * (st.done ?? 0)) / st.total : 0}%` }} /></div>
          <p className="sub" style={{ fontSize: 13, marginTop: 6 }}>
            {num(st.done)} / {num(st.total)}{st.phase === "regrouping" ? " · updating groups…" : ""}
          </p>
          <button className="danger small" onClick={() => api.removalCancel()}>Stop</button>
        </div>
      )}
      {st && !st.running && st.mode === "restore" && st.finished && (
        <div className={`callout ${st.conflicts ? "warn" : ""}`} style={{ marginBottom: 16 }}>
          Restored {num(st.moved)} photo(s) to their original location.
          {!!st.conflicts && ` ${num(st.conflicts)} could not be restored because a file now exists at the same path (left in quarantine, never overwritten).`}
        </div>
      )}

      <div className="card">
        <h2>Batches</h2>
        {!batches.data?.length ? (
          <p className="sub">Nothing has been moved yet.</p>
        ) : (
          <div className="table-scroll">
          <table className="simple">
            <thead>
              <tr><th>When</th><th>Why</th><th>Photos</th><th>Folder</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {batches.data.map((b) => (
                <BatchRow key={b.id} b={b} open={open === b.id} onToggle={() => setOpen(open === b.id ? null : b.id)}
                  onRestore={() =>
                    confirm(`Move ${num(b.in_quarantine)} photo(s) back to where they were?\n\nNothing is overwritten: if a file now exists at the same path, that photo stays in quarantine.`) &&
                    restore.mutate(b.id)
                  }
                  busy={!!st?.running}
                />
              ))}
            </tbody>
          </table>
          </div>
        )}
      </div>
      {moving && <RemoveModal onClose={() => setMoving(false)} />}
    </div>
  );
}

function BatchRow({ b, open, onToggle, onRestore, busy }: { b: RemovalBatch; open: boolean; onToggle: () => void; onRestore: () => void; busy: boolean }) {
  const detail = useQuery({ queryKey: ["removal-batch", b.id], queryFn: () => api.removalBatch(b.id), enabled: open });
  const [label, cls] = STATE_LABEL[b.state] ?? [b.state, ""];
  return (
    <>
      <tr>
        <td>{epoch(b.created)}</td>
        <td>{b.reason === "manual" ? "Manual" : b.reason.replace(/^auto:/, "Auto: ")}</td>
        <td>{num(b.in_quarantine)} <span className="sub">/ {num(b.files)} · {bytes(b.bytes)}</span></td>
        <td className="mono" style={{ overflowWrap: "anywhere", maxWidth: 320 }}>{b.dest}</td>
        <td><span className={`badge ${cls}`}>{label}</span></td>
        <td style={{ whiteSpace: "nowrap" }}>
          <button className="small ghost" onClick={onToggle}>{open ? "Hide" : "Files"}</button>{" "}
          <button className="small" disabled={!b.in_quarantine || busy} onClick={onRestore}>Restore</button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            <div style={{ maxHeight: 300, overflow: "auto" }}>
              <table className="simple">
                <tbody>
                  {detail.data?.items?.map((i) => (
                    <tr key={i.id}>
                      <td className="mono" style={{ overflowWrap: "anywhere" }}>{i.rel_path}</td>
                      <td>{bytes(i.size)}</td>
                      <td><span className={`badge ${i.status === "moved" ? "accent" : i.status === "restored" ? "good" : "warn"}`}>{i.status === "moved" ? "in quarantine" : i.status}</span></td>
                      <td className="sub">{i.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
