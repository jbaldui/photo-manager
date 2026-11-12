import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { num } from "../format";

const LABELS: Record<string, [string, string]> = {
  phash_candidate: ["pHash candidate radius", "Max pHash distance (of 64 bits) to consider two images at all."],
  phash_duplicate: ["pHash duplicate distance", "pHash distance at or below which (with dHash/wHash) images are duplicates."],
  dhash_duplicate: ["dHash / wHash duplicate distance", "Second hash that must also agree for a duplicate."],
  rotated_duplicate: ["Rotated / mirrored distance", "pHash distance against 90°/180°/270°/mirrored variants."],
  trimmed_near: ["Caption / border distance", "pHash distance after stripping caption bars and borders (memes)."],
  clip_candidate: ["CLIP candidate similarity", "Min CLIP cosine for a neighbour to be checked at all."],
  clip_duplicate: ["CLIP duplicate similarity", "With ORB confirmation, above this is a duplicate."],
  clip_near: ["CLIP edited-version similarity", "With ORB confirmation, above this is an edited version."],
  clip_similar: ["CLIP similar (unverified)", "Without ORB confirmation, above this goes to the Similar tier."],
  clip_k: ["CLIP neighbours per image", "How many nearest neighbours to examine per image."],
  text_min_words: ["OCR: min. words to compare", "Images with fewer recognised words are not grouped by text (avoids logos, \"ok\")."],
  text_jaccard: ["OCR: same-text overlap", "Share of words two images must have in common (1.0 = identical text)."],
  orb_min_inliers: ["ORB min. matching keypoints", "Geometrically consistent keypoints needed to confirm an edit."],
  orb_min_ratio: ["ORB min. match ratio", "Inliers / keypoints needed to confirm an edit."],
};

export default function Settings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [vals, setVals] = useState<Record<string, number>>({});
  const [exported, setExported] = useState<{ name: string; url: string; path: string } | null>(null);
  useEffect(() => {
    if (data) setVals(data.values);
  }, [data]);

  const save = useMutation({
    mutationFn: async (rerun: boolean) => {
      await api.putSettings(vals);
      if (rerun) await api.startScan(["pairs", "group"]);
    },
    onSuccess: () => qc.invalidateQueries(),
  });
  const exp = useMutation({ mutationFn: api.exportData, onSuccess: setExported });
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const discards = stats?.decisions.find((d) => d.key === "discard")?.count ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Settings &amp; export</h1>
          <p className="sub">
            Data folder: <span className="mono">{data?.data_dir}</span> · {data?.workers} worker processes
          </p>
        </div>
      </div>
      <div className="grid-2">
        <div className="card">
          <h2>Export</h2>
          <p className="sub" style={{ marginBottom: 12 }}>
            Export every group with its members and your keep/discard decisions. To remove discarded photos, use{" "}
            <Link to="/quarantine">Quarantine</Link>: the app moves them for you and can undo it.
          </p>
          <div className="row">
            <button onClick={() => exp.mutate("csv")}>Groups as CSV</button>
            <button onClick={() => exp.mutate("json")}>Groups as JSON</button>
            {discards > 0 && <Link className="btn" to="/quarantine">Move {num(discards)} discarded to quarantine…</Link>}
          </div>
          {exported && (
            <div className="callout" style={{ marginTop: 12 }}>
              Saved <span className="mono">{exported.path}</span> · <a href={exported.url}>download</a>
            </div>
          )}
        </div>
        <div className="card">
          <div className="row" style={{ marginBottom: 12 }}>
            <h2 className="grow" style={{ margin: 0 }}>Matching thresholds</h2>
            <button className="small" onClick={() => data && setVals(data.defaults)}>Reset defaults</button>
          </div>
          <dl className="kv" style={{ gap: "10px 12px", alignItems: "center" }}>
            {Object.entries(LABELS).map(([k, [label, help]]) => (
              <SettingRow key={k} label={label} help={help} value={vals[k]} onChange={(v) => setVals({ ...vals, [k]: v })} />
            ))}
          </dl>
          <div className="row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
            <button onClick={() => save.mutate(false)}>Save</button>
            <button className="primary" onClick={() => save.mutate(true)}>Save &amp; re-run grouping</button>
          </div>
          {save.isError && <div className="callout error">{(save.error as Error).message}</div>}
          {save.isSuccess && <div className="callout" style={{ marginTop: 8 }}>Saved.</div>}
        </div>
      </div>
    </div>
  );
}

function SettingRow({ label, help, value, onChange }: { label: string; help: string; value: number | undefined; onChange: (v: number) => void }) {
  return (
    <>
      <dt title={help} style={{ color: "var(--text)" }}>
        {label}
        <div style={{ color: "var(--muted)", fontSize: 11 }}>{help}</div>
      </dt>
      <dd>
        <input type="number" step="any" style={{ width: 90 }} value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))} />
      </dd>
    </>
  );
}
