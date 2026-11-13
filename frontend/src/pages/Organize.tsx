import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, OrganizeOptions, OrganizePreview } from "../api";
import { Choice, DEFAULT_ORGANIZE, OrganizeFields } from "../components/OrganizeForm";
import { bytes, duration, num } from "../format";

export default function Organize() {
  const [o, setO] = useState<OrganizeOptions>(DEFAULT_ORGANIZE);
  const [preview, setPreview] = useState<OrganizePreview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const set = <K extends keyof OrganizeOptions>(k: K, v: OrganizeOptions[K]) => {
    setO((x) => ({ ...x, [k]: v }));
    setPreview(null);
  };

  const status = useQuery({
    queryKey: ["organize-status"],
    queryFn: api.organizeStatus,
    refetchInterval: (q) => (q.state.data?.running ? 700 : false),
  });
  const pv = useMutation({ mutationFn: api.organizePreview, onSuccess: (p) => (setPreview(p), setErr(null)), onError: (e: Error) => setErr(e.message) });
  const start = useMutation({
    mutationFn: api.organizeStart,
    onSuccess: () => (setErr(null), status.refetch()),
    onError: (e: Error) => setErr(e.message),
  });
  const st = status.data;
  const running = !!st?.running;
  const enough = preview ? preview.bytes < preview.free_bytes * 0.98 : true;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Organize</h1>
          <p className="sub">
            Creates an organized <strong>copy</strong> of your photos in a new folder. Your original files are never moved, renamed or changed.
          </p>
        </div>
      </div>
      {err && <div className="callout error" style={{ marginBottom: 12 }}>{err}</div>}

      {st && (st.running || st.finished) && st.out && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="row" style={{ marginBottom: 8 }}>
            <h2 className="grow" style={{ margin: 0 }}>
              {st.running ? "Copying…" : st.cancelled ? "Stopped" : "Done"}
            </h2>
            {st.running ? (
              <button className="danger" onClick={() => api.organizeCancel().then(() => status.refetch())}>Stop</button>
            ) : (
              <a className="btn" href="/api/organize/manifest">Download manifest.csv</a>
            )}
          </div>
          <div className="meter" style={{ marginBottom: 6 }}>
            <div style={{ width: `${st.bytes_total ? (100 * (st.bytes_done ?? 0)) / st.bytes_total : 0}%` }} />
          </div>
          <p className="sub" style={{ fontSize: 13 }}>
            {num(st.done)} / {num(st.total)} photos · {bytes(st.bytes_done)} / {bytes(st.bytes_total)}
            {st.running && st.started && st.bytes_done
              ? ` · ETA ${duration(((st.bytes_total! - st.bytes_done) * (Date.now() / 1000 - st.started)) / st.bytes_done)}`
              : ""}
          </p>
          <p className="mono" style={{ fontSize: 12, margin: "6px 0 0", overflowWrap: "anywhere" }}>{st.out}</p>
          {!!st.errors?.length && (
            <details style={{ marginTop: 8 }}>
              <summary className="badge bad">{st.errors.length} files could not be copied</summary>
              <table className="simple">
                <tbody>
                  {st.errors.slice(0, 200).map((e, i) => (
                    <tr key={i}><td className="mono">{e.src}</td><td>{e.error}</td></tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}

      <div className="stack">
        <OrganizeFields
          o={o}
          set={set}
          scope={
            <>
              <Choice
                name="scope"
                value={o.scope}
                onChange={(v) => set("scope", v)}
                options={[
                  { v: "not_discarded", label: "Everything except “discard”", help: "All photos, skipping the ones you marked discard." },
                  { v: "all", label: "All photos", help: "The whole library, ignoring your decisions." },
                  { v: "grouped", label: "Only photos in groups", help: "Only photos that have a duplicate / similar / same-text match." },
                ]}
              />
              <label className="row" style={{ marginTop: 10, fontSize: 13 }}>
                <input type="checkbox" checked={o.one_per_exact} onChange={(e) => set("one_per_exact", e.target.checked)} />
                Copy only one of byte-identical files (the best copy)
              </label>
            </>
          }
        />

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button disabled={!o.dest || pv.isPending || running} onClick={() => pv.mutate(o)}>
            {pv.isPending ? "Calculating…" : "Preview"}
          </button>
          <button
            className="primary"
            disabled={!preview || !preview.files || !enough || running || start.isPending}
            onClick={() =>
              confirm(`Copy ${num(preview!.files)} photos (${bytes(preview!.bytes)}) into a new folder inside\n${o.dest}?\n\nOriginals are not touched.`) &&
              start.mutate(o)
            }
          >
            Copy {preview ? `${num(preview.files)} photos` : ""}
          </button>
        </div>

        {preview && (
          <div className="card">
            <h2>Preview</h2>
            <div className="tiles">
              <div className="tile"><div className="label">Photos</div><div className="value">{num(preview.files)}</div></div>
              <div className="tile"><div className="label">Size</div><div className="value">{bytes(preview.bytes)}</div>
                <div className="hint">{bytes(preview.free_bytes)} free {enough ? "" : "— not enough space"}</div></div>
              <div className="tile"><div className="label">Groups</div><div className="value">{num(preview.groups)}</div></div>
              <div className="tile"><div className="label">Renamed</div><div className="value">{num(preview.renamed)}</div></div>
            </div>
            {!enough && <div className="callout error" style={{ marginBottom: 8 }}>Not enough free space at the destination.</div>}
            <div style={{ maxHeight: 420, overflow: "auto" }}>
              <table className="simple">
                <thead><tr><th>Original</th><th>→ Copy</th></tr></thead>
                <tbody>
                  {preview.sample.map((s) => (
                    <tr key={s.file_id}>
                      <td className="mono" style={{ overflowWrap: "anywhere" }}>{s.src}</td>
                      <td className="mono" style={{ overflowWrap: "anywhere" }}>
                        {s.dst} {s.is_best && <span className="badge good">best</span>} {s.renamed && <span className="badge">renamed</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {preview.files > preview.sample.length && (
              <p className="sub" style={{ fontSize: 12 }}>Showing the first {preview.sample.length} of {num(preview.files)}. The full list is written to manifest.csv.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
