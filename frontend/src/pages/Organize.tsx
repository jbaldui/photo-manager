import { ReactNode, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, OrganizeOptions, OrganizePreview, Tier } from "../api";
import FolderPicker from "../components/FolderPicker";
import { bytes, duration, num, TIER_INFO } from "../format";

const TIERS: Tier[] = ["exact", "duplicate", "near", "similar", "text"];

function Choice<T extends string>({
  value,
  onChange,
  options,
  name,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { v: T; label: string; help: string }[];
  name: string;
}) {
  return (
    <div className="choices" role="radiogroup">
      {options.map((o) => (
        <label key={o.v} className={`choice ${value === o.v ? "on" : ""}`}>
          <input type="radio" name={name} checked={value === o.v} onChange={() => onChange(o.v)} />
          <span>
            <strong>{o.label}</strong>
            <span className="sub" style={{ display: "block", fontSize: 12 }}>{o.help}</span>
          </span>
        </label>
      ))}
    </div>
  );
}

function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <div className="card">
      <h2><span className="step">{n}</span>{title}</h2>
      {children}
    </div>
  );
}

const EXAMPLE_NAMES: Record<OrganizeOptions["pattern"], string> = {
  date: "2019-08-12_15-30-12.jpg",
  date_name: "2019-08-12_15-30-12_IMG_1234.jpg",
  group: "g0003_01_IMG_1234.jpg  (01 = best copy)",
};

export default function Organize() {
  const [picking, setPicking] = useState(false);
  const [o, setO] = useState<OrganizeOptions>({
    dest: "",
    scope: "not_discarded",
    layout: "groups",
    tiers: ["exact", "duplicate", "near"],
    one_per_exact: false,
    rename: "duplicates",
    pattern: "date_name",
  });
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
  const needsTiers = o.layout === "groups" || o.scope === "grouped" || o.rename === "duplicates" || o.pattern === "group";
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
        <Section n={1} title="Destination">
          <div className="row">
            <input className="grow mono" readOnly value={o.dest} placeholder="Choose where the organized copy will be created…" onClick={() => setPicking(true)} />
            <button onClick={() => setPicking(true)}>Choose folder</button>
          </div>
          <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
            A new folder named “Organized &lt;date time&gt;” is created inside it, so nothing there is ever overwritten. It must be outside your scanned folders.
          </p>
        </Section>

        <Section n={2} title="Which photos">
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
        </Section>

        <Section n={3} title="Folder layout">
          <Choice
            name="layout"
            value={o.layout}
            onChange={(v) => set("layout", v)}
            options={[
              { v: "groups", label: "One sub-folder per group", help: "g0001_<name>_(5)/ … for each group; photos with no match go to _unique/." },
              { v: "flat", label: "A single folder", help: "Every photo side by side in one folder." },
            ]}
          />
          {needsTiers && (
            <div style={{ marginTop: 12 }}>
              <h3>Groups are made of</h3>
              <div className="row">
                {TIERS.map((t) => (
                  <label key={t} className={`badge ${o.tiers.includes(t) ? "accent" : ""}`} style={{ cursor: "pointer", padding: "5px 10px", fontSize: 12 }}>
                    <input
                      type="checkbox"
                      style={{ margin: "0 4px 0 0" }}
                      checked={o.tiers.includes(t)}
                      onChange={(e) => set("tiers", e.target.checked ? [...o.tiers, t] : o.tiers.filter((x) => x !== t))}
                    />
                    {TIER_INFO[t].label}
                  </label>
                ))}
              </div>
              <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>Overlapping groups are merged, so each photo is copied once.</p>
            </div>
          )}
        </Section>

        <Section n={4} title="File names">
          <Choice
            name="rename"
            value={o.rename}
            onChange={(v) => set("rename", v)}
            options={[
              { v: "keep", label: "Keep original names", help: "Only clashes are renamed: photo.jpg, photo_2.jpg, photo_3.jpg…" },
              { v: "duplicates", label: "Rename photos in groups", help: "Duplicates get a clear, unique name; other photos keep theirs." },
              { v: "all", label: "Rename all photos", help: "Every photo gets a name from the pattern below." },
            ]}
          />
          {o.rename !== "keep" && (
            <div className="row" style={{ marginTop: 10 }}>
              <span style={{ fontSize: 13 }}>Pattern</span>
              <select value={o.pattern} onChange={(e) => set("pattern", e.target.value as OrganizeOptions["pattern"])}>
                <option value="date_name">Date + original name</option>
                <option value="date">Date only</option>
                <option value="group">Group number + original name</option>
              </select>
              <span className="mono sub" style={{ fontSize: 12 }}>{EXAMPLE_NAMES[o.pattern]}</span>
            </div>
          )}
          <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>
            Date = date taken (EXIF or file name), otherwise the last-modified date. Names never collide (checked case-insensitively, safe for Windows/USB drives).
          </p>
        </Section>

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
      {picking && (
        <FolderPicker
          title="Choose the destination"
          note="A new sub-folder will be created here."
          onClose={() => setPicking(false)}
          onPick={(p) => {
            set("dest", p);
            setPicking(false);
          }}
        />
      )}
    </div>
  );
}
