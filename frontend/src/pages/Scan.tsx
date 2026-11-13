import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useScanStatus } from "../App";
import FolderPicker from "../components/FolderPicker";
import { bytes, duration, num } from "../format";

const STAGE_HELP: Record<string, string> = {
  scan: "Walks the folders and finds new, changed and removed files.",
  analyze: "Reads each file once: SHA-256, EXIF/metadata, 4 perceptual hashes (pHash, dHash, wHash, colour) and a thumbnail.",
  embed: "Runs the CLIP neural network on every unique image. Slowest step on CPU; it resumes where it stopped.",
  ocr: "Reads text inside images (OCR). Images most likely to contain text (screenshots, memes, documents) go first; resumes where it stopped.",
  faces: "Finds faces (YuNet) and recognises them (SFace), then groups them into people. Runs locally; resumes where it stopped.",
  pairs: "Finds candidate pairs with hash and CLIP indices, then confirms edits with ORB keypoint matching.",
  group: "Builds the exact / duplicate / edited / similar groups.",
};

export default function Scan() {
  const qc = useQueryClient();
  const [picking, setPicking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [withClip, setWithClip] = useState(true);
  const [withOcr, setWithOcr] = useState(true);
  const [withFaces, setWithFaces] = useState(true);
  const roots = useQuery({ queryKey: ["roots"], queryFn: api.roots, refetchInterval: 5000 });
  const { data: scan } = useScanStatus();
  const errors = useQuery({ queryKey: ["errors"], queryFn: api.errors });

  const add = useMutation({
    mutationFn: api.addRoot,
    onSuccess: () => {
      setPicking(false);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["roots"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const [removed, setRemoved] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: api.removeRoot,
    onSuccess: (r) => {
      setErr(null);
      setRemoved(
        `Removed ${num(r.removed.files)} files from the library` +
          (r.removed.images ? `, plus ${num(r.removed.images)} images that existed only in that folder` : "") +
          ". Duplicate groups were rebuilt.",
      );
      qc.invalidateQueries();
    },
    onError: (e: Error) => setErr(e.message),
  });
  const start = useMutation({
    mutationFn: (stages?: string[]) => api.startScan(stages),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scan-status"] }),
    onError: (e: Error) => setErr(e.message),
  });

  const running = !!scan?.running;
  const allStages = ["scan", "analyze", "embed", "ocr", "faces", "pairs", "group"];
  const chosen = allStages.filter((s) => (s !== "embed" || withClip) && (s !== "ocr" || withOcr) && (s !== "faces" || withFaces));

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Folders &amp; scan</h1>
          <p className="sub">Photos are only ever read. All results stay in the app's local data folder.</p>
        </div>
      </div>
      {err && <div className="callout error" style={{ marginBottom: 12 }}>{err}</div>}
      {removed && <div className="callout" style={{ marginBottom: 12 }}>{removed}</div>}

      <div className="stack">
        <div className="card">
          <div className="row" style={{ marginBottom: 12 }}>
            <div className="grow">
              <h2 style={{ margin: 0 }}>Photo folders</h2>
              <p className="sub" style={{ fontSize: 12 }}>Each folder is scanned with all its sub-folders (hidden folders starting with “.” are skipped).</p>
            </div>
            <button className="primary" onClick={() => setPicking(true)} disabled={running}>+ Add folder</button>
          </div>
          {roots.data?.length ? (
            <table className="simple">
              <thead>
                <tr><th>Folder</th><th>Photos</th><th>Pending</th><th>Unreadable</th><th>Missing</th><th /></tr>
              </thead>
              <tbody>
                {roots.data.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">
                      {r.path} {!r.exists && <span className="badge bad">not found</span>}
                    </td>
                    <td className="mono">{num(r.ok)}</td>
                    <td className="mono">{num(r.pending)}</td>
                    <td className="mono">{num(r.errors)}</td>
                    <td className="mono">{num(r.missing)}</td>
                    <td style={{ textAlign: "right" }}>
                      <button
                        className="small danger"
                        disabled={running}
                        title="Remove this folder and everything found in it from the library (files on disk are not touched)"
                        onClick={() =>
                          confirm(
                            `Remove ${r.path} from the library?\n\n` +
                              `• Its ${num(r.files)} photos disappear from the library, duplicate groups and search\n` +
                              `• Photos in other folders stop being listed as duplicates of them\n` +
                              `• Keep/discard marks for this folder are forgotten\n\n` +
                              `Your files on disk are NOT touched.`,
                          ) && remove.mutate(r.id)
                        }
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="sub">No folders yet. Add the folder that contains your photos — every sub-folder inside it is scanned too, at any depth.</p>
          )}
        </div>

        <div className="card">
          <div className="row" style={{ marginBottom: 8 }}>
            <h2 className="grow" style={{ margin: 0 }}>Analysis pipeline</h2>
            <label className="row" style={{ fontSize: 13, color: "var(--text-2)" }}>
              <input type="checkbox" checked={withClip} onChange={(e) => setWithClip(e.target.checked)} disabled={running} />
              CLIP (edited/similar detection, visual search)
            </label>
            <label className="row" style={{ fontSize: 13, color: "var(--text-2)" }}>
              <input type="checkbox" checked={withOcr} onChange={(e) => setWithOcr(e.target.checked)} disabled={running} />
              OCR (text search, same-text groups)
            </label>
            <label className="row" style={{ fontSize: 13, color: "var(--text-2)" }}>
              <input type="checkbox" checked={withFaces} onChange={(e) => setWithFaces(e.target.checked)} disabled={running} />
              Faces (find photos by person)
            </label>
            {running ? (
              <button className="danger" onClick={() => api.cancelScan().then(() => qc.invalidateQueries({ queryKey: ["scan-status"] }))}>
                Stop
              </button>
            ) : (
              <>
                <button
                  disabled={!roots.data?.length}
                  onClick={() => start.mutate(["scan", "group"])}
                  title="Fast: only checks which files were added, moved or deleted outside the app (e.g. in your file manager) and updates the groups"
                >
                  Check for moved/deleted files
                </button>
                <button onClick={() => start.mutate(["pairs", "group"])} title="Re-run matching with the current settings">
                  Re-run grouping
                </button>
                <button className="primary" disabled={!roots.data?.length} onClick={() => start.mutate(chosen)}>
                  Scan now
                </button>
              </>
            )}
          </div>
          <p className="sub" style={{ fontSize: 12, marginBottom: 8 }}>
            Scans are incremental: unchanged files are skipped, and a stopped scan resumes where it left off.
          </p>
          {scan?.error && <div className="callout error">{scan.error}</div>}
          <div>
            {allStages.map((k) => {
              const st = scan?.stages[k];
              if (!st) return null;
              const frac = st.total ? st.processed / st.total : 0;
              return (
                <div className="stage" key={k}>
                  <span className={`dot ${st.status}`} aria-label={st.status} />
                  <div>
                    <div style={{ fontWeight: 600 }}>{st.label}</div>
                    <div className="sub" style={{ fontSize: 12 }}>{st.message || STAGE_HELP[k]}</div>
                    {st.status === "running" && st.total ? (
                      <div className="meter" style={{ marginTop: 6 }}>
                        <div style={{ width: `${frac * 100}%` }} />
                      </div>
                    ) : null}
                  </div>
                  <div style={{ textAlign: "right", fontSize: 12, color: "var(--text-2)" }}>
                    {st.status === "running" && st.eta_seconds != null && <div>ETA {duration(st.eta_seconds)}</div>}
                    {st.status === "running" && st.rate != null && <div>{st.rate}/s</div>}
                    {st.status !== "running" && st.status !== "queued" && <span className="badge">{st.status}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="card" id="errors">
          <h2>Unreadable files {errors.data?.length ? `(${num(errors.data.length)})` : ""}</h2>
          {errors.data?.length ? (
            <div style={{ maxHeight: 320, overflow: "auto" }}>
              <table className="simple">
                <thead><tr><th>File</th><th>Size</th><th>Error</th></tr></thead>
                <tbody>
                  {errors.data.map((e) => (
                    <tr key={e.id}>
                      <td className="mono">{e.rel_path}</td>
                      <td className="mono">{bytes(e.size)}</td>
                      <td>{e.error}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="sub">None.</p>
          )}
        </div>
      </div>

      {picking && <FolderPicker onClose={() => setPicking(false)} onPick={(p) => add.mutate(p)} />}
    </div>
  );
}
