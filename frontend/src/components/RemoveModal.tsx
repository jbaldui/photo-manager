import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, JobStatus, RemovalOptions } from "../api";
import { bytes, num } from "../format";
import { FolderField } from "./OrganizeForm";
import { Modal } from "./ui";

/** Preview + confirm moving discarded photos to quarantine, then follow progress. */
export default function RemoveModal({ groupId, onClose }: { groupId?: number; onClose: () => void }) {
  const qc = useQueryClient();
  const defaults = useQuery({ queryKey: ["removal-defaults"], queryFn: api.removalDefaults });
  const [dest, setDest] = useState("");
  useEffect(() => {
    if (defaults.data && !dest) setDest(defaults.data.dest);
  }, [defaults.data, dest]);
  const opts: RemovalOptions = { dest, scope: "discarded", group_id: groupId ?? null };
  const preview = useQuery({
    queryKey: ["removal-preview", dest, groupId],
    queryFn: () => api.removalPreview(opts),
    enabled: !!dest,
    retry: false,
  });
  const [job, setJob] = useState<JobStatus | null>(null);
  const status = useQuery({
    queryKey: ["removal-status"],
    queryFn: api.removalStatus,
    enabled: !!job,
    refetchInterval: (q) => (q.state.data?.running ? 500 : false),
  });
  const start = useMutation({
    mutationFn: () => api.removalStart(opts),
    onSuccess: (s) => {
      setJob(s);
      qc.setQueryData(["removal-status"], s);
      qc.invalidateQueries({ queryKey: ["removal-status"] });
    },
  });
  const st = job ? status.data ?? job : null;
  const done = st && !st.running;
  const p = preview.data;

  return (
    <Modal onClose={onClose}>
      <div className="modal" role="dialog" aria-label="Move to quarantine">
        <header>
          <h2 className="grow" style={{ margin: 0 }}>{st ? (done ? "Moved to quarantine" : "Moving to quarantine…") : "Move discarded photos to quarantine"}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body stack">
          {!st && (
            <>
              <p className="sub" style={{ fontSize: 13 }}>
                Photos marked <strong>discard</strong>{groupId ? " in this group" : ""} are <strong>moved</strong> out of your library into the folder below, keeping their
                folder structure. Nothing is deleted, and you can put everything back with one click from the Quarantine page.
              </p>
              <div>
                <h3>Quarantine folder</h3>
                <FolderField
                  value={dest}
                  onChange={setDest}
                  placeholder="Folder to move discarded photos into"
                  title="Choose the quarantine folder"
                  note="A dated sub-folder is created for each batch. Must be outside your scanned folders."
                />
              </div>
              {preview.error && <div className="callout error">{(preview.error as Error).message}</div>}
              {p && (
                <>
                  <div className="tiles" style={{ marginBottom: 0 }}>
                    <div className="tile"><div className="label">Photos to move</div><div className="value">{num(p.files)}</div></div>
                    <div className="tile"><div className="label">Size</div><div className="value">{bytes(p.bytes)}</div>
                      <div className="hint">{p.same_device ? "same drive: instant" : `different drive: copied, verified, then removed · ${bytes(p.free_bytes)} free`}</div></div>
                  </div>
                  {!p.dest_exists && <p className="sub" style={{ fontSize: 12 }}>The folder will be created.</p>}
                  {p.blocked.length > 0 && (
                    <div className="callout warn">
                      {num(p.blocked.length)} photo(s) stay in place because every copy in their group is marked discard. The best copy is always kept.
                    </div>
                  )}
                  {p.files > 0 && (
                    <details>
                      <summary className="sub" style={{ cursor: "pointer", fontSize: 13 }}>Show files</summary>
                      <div style={{ maxHeight: 220, overflow: "auto", marginTop: 6 }}>
                        <table className="simple">
                          <tbody>
                            {p.sample.map((s) => (
                              <tr key={s.file_id}><td className="mono" style={{ overflowWrap: "anywhere" }}>{s.src}</td><td>{bytes(s.size)}</td></tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  )}
                </>
              )}
              {start.error && <div className="callout error">{(start.error as Error).message}</div>}
            </>
          )}
          {st && (
            <>
              <div className="meter"><div style={{ width: `${st.total ? (100 * (st.done ?? 0)) / st.total : 0}%` }} /></div>
              <p className="sub" style={{ fontSize: 13 }}>
                {num(st.done)} / {num(st.total)} · {bytes(st.bytes_done)} / {bytes(st.bytes_total)}
                {st.phase === "regrouping" && " · updating groups…"}
              </p>
              {done && (
                <div className={`callout ${st.errors?.length ? "warn" : ""}`}>
                  {num(st.moved)} photo(s) moved to <span className="mono" style={{ overflowWrap: "anywhere" }}>{st.dest}</span>.
                  {!!st.skipped && ` ${num(st.skipped)} skipped (changed or missing since the last scan).`}
                  {!!st.errors?.length && ` ${st.errors.length} could not be moved.`} Groups and counts are already updated.
                </div>
              )}
              {!!st.errors?.length && (
                <details>
                  <summary className="sub" style={{ cursor: "pointer", fontSize: 13 }}>Details</summary>
                  <table className="simple"><tbody>
                    {st.errors.slice(0, 100).map((e, i) => <tr key={i}><td className="mono">{e.src}</td><td>{e.error}</td></tr>)}
                  </tbody></table>
                </details>
              )}
            </>
          )}
        </div>
        <footer className="row" style={{ justifyContent: "flex-end" }}>
          {!st && (
            <>
              <button onClick={onClose}>Cancel</button>
              <button className="danger" disabled={!p?.files || start.isPending} onClick={() => start.mutate()}>
                Move {p ? num(p.files) : ""} photos
              </button>
            </>
          )}
          {st?.running && <button className="danger" onClick={() => api.removalCancel()}>Stop</button>}
          {done && (
            <>
              <Link className="btn" to="/quarantine" onClick={onClose}>Open Quarantine (undo)</Link>
              <button className="primary" onClick={onClose}>Close</button>
            </>
          )}
        </footer>
      </div>
    </Modal>
  );
}
