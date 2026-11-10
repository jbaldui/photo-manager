import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Lightbox, ScoreBadges, Thumb } from "../components/ui";
import { bytes, DATE_SOURCE_LABEL, dateTime, epoch, TIER_INFO } from "../format";

export default function PhotoPage() {
  const { id } = useParams();
  const pid = Number(id);
  const nav = useNavigate();
  const qc = useQueryClient();
  const [zoom, setZoom] = useState(false);
  const [showBoxes, setShowBoxes] = useState(false);
  const { data: p, isLoading, error } = useQuery({ queryKey: ["photo", pid], queryFn: () => api.photo(pid) });
  const sim = useQuery({ queryKey: ["similar", pid], queryFn: () => api.similar(pid), enabled: !!p?.hashes?.clip_embedded });
  const decide = useMutation({
    mutationFn: (d: "keep" | "discard" | "clear") => api.decide([pid], d),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["photo", pid] }),
  });

  if (isLoading) return <div className="page"><p className="sub">Loading…</p></div>;
  if (error || !p) return <div className="page"><div className="callout error">{(error as Error)?.message ?? "Not found"}</div></div>;

  const rows: [string, React.ReactNode][] = [
    ["Path", <span className="mono">{p.abs_path}</span>],
    ["Format", `${p.format ?? "?"} · .${p.ext}${p.frames && p.frames > 1 ? ` · ${p.frames} frames` : ""}`],
    ["Resolution", p.width ? `${p.width} × ${p.height} (${((p.width * (p.height ?? 0)) / 1e6).toFixed(1)} MP)` : "–"],
    ["File size", bytes(p.size)],
    ["Date taken", `${dateTime(p.date_taken)} · ${DATE_SOURCE_LABEL[p.date_source ?? "none"]}`],
    ["Last modified", epoch(p.mtime)],
    ["Changed (ctime)", epoch(p.ctime)],
    ["Camera", [p.camera_make, p.camera_model].filter(Boolean).join(" ") || "–"],
    ["Lens", p.lens ?? "–"],
    ["Software", p.software ?? "–"],
    ["Likely source", p.source_guess ?? "–"],
    ["GPS", p.gps_lat != null ? `${p.gps_lat}, ${p.gps_lon}` : "–"],
    ["Colour mode", `${p.mode ?? "?"}${p.has_alpha ? " · transparency" : ""}`],
  ];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <button className="small ghost" onClick={() => nav(-1)}>← Back</button>
          <h1 style={{ overflowWrap: "anywhere" }}>{p.name}</h1>
          <p className="sub mono">{p.rel_path}</p>
        </div>
        <div className="row">
          <button className={p.decision === "keep" ? "on-keep" : ""} onClick={() => decide.mutate(p.decision === "keep" ? "clear" : "keep")}>✓ Keep</button>
          <button className={p.decision === "discard" ? "on-discard" : ""} onClick={() => decide.mutate(p.decision === "discard" ? "clear" : "discard")}>✕ Discard</button>
          <a className="btn" href={`/api/photos/${p.id}/original`} target="_blank" rel="noreferrer">Open original</a>
        </div>
      </div>
      {p.status === "error" && <div className="callout error" style={{ marginBottom: 12 }}>Could not decode: {p.error}</div>}

      <div className="detail">
        <div className="stack">
          <div className="preview-box" onClick={() => setZoom(true)} style={{ cursor: "zoom-in" }}>
            <span className="preview-wrap">
              <img src={`/api/photos/${p.id}/preview`} alt={p.name} />
              {showBoxes &&
                p.ocr?.lines.map(([t, conf, [x0, y0, x1, y1]], i) => (
                  <span
                    key={i}
                    className="ocr-box"
                    title={`${t} (${Math.round(conf * 100)}%)`}
                    style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                  />
                ))}
            </span>
          </div>
          {p.ocr && p.ocr.n_words > 0 && (
            <div className="card">
              <div className="row" style={{ marginBottom: 8 }}>
                <h2 className="grow" style={{ margin: 0 }}>Text in image</h2>
                <label className="row" style={{ fontSize: 12, color: "var(--text-2)" }}>
                  <input type="checkbox" checked={showBoxes} onChange={(e) => setShowBoxes(e.target.checked)} /> Outline on photo
                </label>
                <button className="small" onClick={() => navigator.clipboard?.writeText(p.ocr!.text)}>Copy</button>
                <button
                  className="small"
                  onClick={() => nav(`/search?mode=text&q=${encodeURIComponent(`"${p.ocr!.lines[0]?.[0] ?? ""}"`)}`)}
                  title="Find other images containing the first line of this text"
                >
                  Find images with this text
                </button>
              </div>
              <div className="ocr-text">
                {p.ocr.lines.map(([t, conf], i) => (
                  <div key={i} title={`confidence ${Math.round(conf * 100)}%`} style={{ opacity: conf < 0.85 ? 0.7 : 1 }}>
                    {t}
                  </div>
                ))}
              </div>
            </div>
          )}
          {(p.groups.length > 0 || p.same_content.length > 0) && (
            <div className="card">
              <h2>Duplicates of this photo</h2>
              <div className="row" style={{ marginBottom: 8 }}>
                {p.groups.map((g) => (
                  <Link key={g.id} to={`/groups/${g.id}`}>
                    <span className="badge accent">{TIER_INFO[g.tier].label}: {g.size} files →</span>
                  </Link>
                ))}
              </div>
              {p.same_content.map((s) => (
                <div key={s.id} className="row" style={{ fontSize: 12 }}>
                  <span className="badge good">identical</span>
                  <Link className="mono" to={`/photo/${s.id}`}>{s.rel_path}</Link>
                  <span className="sub">modified {epoch(s.mtime)}</span>
                </div>
              ))}
            </div>
          )}
          <div className="card">
            <h2>Visually similar</h2>
            {!p.hashes?.clip_embedded ? (
              <p className="sub">Available after the CLIP stage has processed this photo.</p>
            ) : sim.isLoading ? (
              <p className="sub">Searching…</p>
            ) : (
              <div className="grid-list" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}>
                {sim.data?.items.map((s) => (
                  <div key={s.id}>
                    <Thumb photo={s} onClick={() => nav(`/photo/${s.id}`)} caption={`CLIP ${s.score?.toFixed(3)}`} />
                    {s.pair && <div style={{ marginTop: 4 }}><ScoreBadges p={s.pair} /></div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <h2>Details</h2>
            <dl className="kv">
              {rows.map(([k, v]) => (
                <Row key={k} k={k} v={v} />
              ))}
            </dl>
          </div>
          {p.hashes && (
            <div className="card">
              <h2>Fingerprints</h2>
              <dl className="kv mono">
                <dt>SHA-256</dt><dd>{p.hashes.sha256}</dd>
                <dt>pHash</dt><dd>{p.hashes.phash}</dd>
                <dt>dHash</dt><dd>{p.hashes.dhash}</dd>
                <dt>wHash</dt><dd>{p.hashes.whash}</dd>
                <dt>colour</dt><dd>{p.hashes.colorhash}</dd>
                <dt>CLIP</dt><dd>{p.hashes.clip_embedded ? "embedded" : "pending"}</dd>
                <dt>OCR</dt><dd>{p.ocr ? (p.ocr.error ? `failed: ${p.ocr.error}` : `${p.ocr.n_words} words`) : "pending"}</dd>
              </dl>
            </div>
          )}
          <div className="card">
            <h2>All metadata {p.exif ? `(${Object.keys(p.exif).length} tags)` : ""}</h2>
            {p.exif ? (
              <dl className="kv exif">
                {Object.entries(p.exif).map(([k, v]) => (
                  <Row key={k} k={k} v={typeof v === "object" ? JSON.stringify(v) : String(v)} />
                ))}
              </dl>
            ) : (
              <p className="sub">This file carries no embedded metadata.</p>
            )}
          </div>
        </div>
      </div>
      {zoom && <Lightbox src={`/api/photos/${p.id}/preview?size=3000`} onClose={() => setZoom(false)} />}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}
