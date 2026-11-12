import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, EnhanceOptions } from "../api";
import { bytes, duration, num } from "../format";
import BeforeAfter from "./BeforeAfter";
import { Modal } from "./ui";

const MODELS: { v: EnhanceOptions["model"]; label: string; help: string }[] = [
  { v: "fast", label: "Fast", help: "realesr-general-x4v3 · good for most photos and memes" },
  { v: "quality", label: "Best quality", help: "RealESRGAN x4plus · sharper, ~12× slower" },
  { v: "none", label: "No upscaling", help: "Only merge the copies" },
];

/** Merge the copies of a group (or enhance one photo) into a new image. Originals are never changed. */
export default function EnhanceModal({ groupId, fileIds, onClose }: { groupId?: number; fileIds?: number[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [o, setO] = useState<EnhanceOptions>({ group_id: groupId ?? null, file_ids: fileIds ?? [], fuse: true, model: "fast", scale: groupId ? 1 : 2 });
  const set = <K extends keyof EnhanceOptions>(k: K, v: EnhanceOptions[K]) => setO((x) => ({ ...x, [k]: v }));
  const est = useQuery({ queryKey: ["enhance-estimate", o], queryFn: () => api.enhanceEstimate(o), retry: false });
  const [started, setStarted] = useState(false);
  const status = useQuery({
    queryKey: ["enhance-status"],
    queryFn: api.enhanceStatus,
    enabled: started,
    refetchInterval: (q) => (q.state.data?.running ? 700 : false),
  });
  const start = useMutation({
    mutationFn: () => api.enhanceStart(o),
    onSuccess: (s) => {
      setStarted(true);
      qc.setQueryData(["enhance-status"], s);
      qc.invalidateQueries({ queryKey: ["enhance-status"] });
    },
  });
  const st = started ? status.data : null;
  const e = est.data;
  const result = st && !st.running ? st.result : null;
  const elapsed = st?.started ? Date.now() / 1000 - st.started : 0;
  const eta = st?.running && st.progress && st.progress > 0.05 ? (elapsed / st.progress) * (1 - st.progress) : st?.estimate?.seconds;

  return (
    <Modal onClose={onClose}>
      <div className="modal wide" role="dialog" aria-label="Enhance">
        <header>
          <h2 className="grow" style={{ margin: 0 }}>{result ? "Enhanced image" : "Enhance"}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>
        <div className="modal-body stack">
          {!st && (
            <>
              <p className="sub" style={{ fontSize: 13 }}>
                {groupId
                  ? "Lines up every copy of this picture, merges them to remove noise and compression artefacts, then optionally upscales the result with a neural network."
                  : "Upscales this photo with a neural network (Real-ESRGAN), on your computer."}{" "}
                The result is saved as a <strong>new file</strong>; your originals are not changed.
              </p>
              {groupId && (
                <label className="row" style={{ fontSize: 13 }}>
                  <input type="checkbox" checked={o.fuse} onChange={(ev) => set("fuse", ev.target.checked)} /> Merge the copies (multi-image fusion)
                </label>
              )}
              <div>
                <h3>Neural upscaling</h3>
                <div className="choices">
                  {MODELS.map((m) => (
                    <label key={m.v} className={`choice ${o.model === m.v ? "on" : ""}`}>
                      <input type="radio" name="model" checked={o.model === m.v} onChange={() => set("model", m.v)} />
                      <span><strong>{m.label}</strong><span className="sub" style={{ display: "block", fontSize: 12 }}>{m.help}</span></span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="row">
                <h3 style={{ margin: 0 }}>Output size</h3>
                <div className="seg">
                  {([1, 2, 4] as const).map((s) => (
                    <button key={s} className={o.scale === s ? "active" : ""} onClick={() => set("scale", s)}>
                      {s === 1 ? "Same size" : `${s}×`}
                    </button>
                  ))}
                </div>
              </div>
              {est.error && <div className="callout error">{(est.error as Error).message}</div>}
              {e && (
                <div className="callout">
                  {e.fuse ? `Merges ${e.sources} different copies` : "Uses the best copy"} ({e.reference.width} × {e.reference.height}) →{" "}
                  <strong>{e.output.width} × {e.output.height}</strong>
                  {e.super_resolution ? " with neural upscaling" : ""}. Estimated time: <strong>{duration(e.seconds)}</strong>.
                  {e.super_resolution && !e.model_downloaded && " The model is downloaded once from GitHub (Real-ESRGAN releases) and then works offline."}
                  {e.notes.map((n) => <div key={n} style={{ marginTop: 4 }}>{n}</div>)}
                </div>
              )}
              {start.error && <div className="callout error">{(start.error as Error).message}</div>}
              <p className="sub" style={{ fontSize: 12 }}>Neural upscaling invents plausible detail. Great for viewing and sharing, but it is not a recovered original.</p>
            </>
          )}
          {st?.running && (
            <>
              <div className="meter"><div style={{ width: `${100 * (st.progress ?? 0)}%` }} /></div>
              <p className="sub" style={{ fontSize: 13 }}>{st.message}{eta ? ` · about ${duration(eta)} left` : ""}</p>
            </>
          )}
          {st && !st.running && st.error && <div className="callout error">{st.error}</div>}
          {st && !st.running && st.cancelled && <div className="callout warn">Stopped.</div>}
          {result && (
            <>
              <BeforeAfter before={`/api/photos/${result.params.sources[0].id}/preview?size=1600`} after={result.image_url} />
              <p className="sub" style={{ fontSize: 13 }}>
                {result.width} × {result.height} · {bytes(result.bytes)} ·{" "}
                {result.params.fused
                  ? `merged ${num(result.params.sources.filter((s) => s.role !== "reference" && s.role === "merged").length + 1)} copies`
                  : "single photo"}
                {result.params.sources.some((s) => s.role.startsWith("skipped")) &&
                  ` · ${result.params.sources.filter((s) => s.role.startsWith("skipped")).length} copies did not line up and were left out`}
              </p>
            </>
          )}
        </div>
        <footer>
          {!st && (
            <>
              <button onClick={onClose}>Cancel</button>
              <button className="primary" disabled={!e || start.isPending} onClick={() => start.mutate()}>Enhance</button>
            </>
          )}
          {st?.running && <button className="danger" onClick={() => api.enhanceCancel()}>Stop</button>}
          {result && (
            <>
              <Link className="btn" to="/enhanced" onClick={onClose}>All enhanced images</Link>
              <a className="btn" href={`${result.image_url}?download=1`}>Download</a>
              <button className="primary" onClick={onClose}>Close</button>
            </>
          )}
          {st && !st.running && !result && <button onClick={() => setStarted(false)}>Back</button>}
        </footer>
      </div>
    </Modal>
  );
}
