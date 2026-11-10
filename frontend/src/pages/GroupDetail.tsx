import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, GroupMember } from "../api";
import { Lightbox, ScoreBadges } from "../components/ui";
import { bytes, DATE_SOURCE_LABEL, dateTime, epoch, TIER_INFO } from "../format";

type Field = { label: string; get: (m: GroupMember) => string };
const FIELDS: Field[] = [
  { label: "Resolution", get: (m) => (m.width ? `${m.width} × ${m.height}` : "–") },
  { label: "File size", get: (m) => bytes(m.size) },
  { label: "Format", get: (m) => `${m.format ?? "?"} (.${m.ext})` },
  { label: "Date taken", get: (m) => (m.date_taken ? `${dateTime(m.date_taken)}` : "–") },
  { label: "Date from", get: (m) => DATE_SOURCE_LABEL[m.date_source ?? "none"] ?? m.date_source ?? "–" },
  { label: "Modified", get: (m) => epoch(m.mtime) },
  { label: "Camera", get: (m) => [m.camera_make, m.camera_model].filter(Boolean).join(" ") || "–" },
  { label: "Software", get: (m) => m.software ?? "–" },
  { label: "Source", get: (m) => m.source_guess ?? "–" },
  { label: "EXIF", get: (m) => (m.has_exif ? "yes" : "no") },
];

export default function GroupDetail() {
  const { id } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [zoom, setZoom] = useState<string | null>(null);
  const { data: g, isLoading } = useQuery({ queryKey: ["group", id], queryFn: () => api.group(Number(id)) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["group", id] });
    qc.invalidateQueries({ queryKey: ["groups"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const decide = useMutation({ mutationFn: (a: { ids: number[]; d: "keep" | "discard" | "clear" }) => api.decide(a.ids, a.d), onSuccess: refresh });
  const auto = useMutation({ mutationFn: () => api.autoDecide(Number(id)), onSuccess: refresh });

  if (isLoading || !g) return <div className="page"><p className="sub">Loading…</p></div>;
  const members = g.members ?? [];
  const differs = (f: Field) => new Set(members.map(f.get)).size > 1;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="row" style={{ marginBottom: 4 }}>
            <button className="small ghost" onClick={() => nav(-1)}>← Back</button>
            <span className="badge accent">{TIER_INFO[g.tier].label}</span>
          </div>
          <h1>{g.tier === "text" ? `${members.length} images with the same text` : `${members.length} versions of this image`}</h1>
          <p className="sub">
            {bytes(g.total_bytes)} total
            {g.tier !== "similar" && g.tier !== "text" && <> · keeping only the best frees {bytes(g.reclaimable_bytes)}</>}. Fields that differ are highlighted.
          </p>
        </div>
        <div className="row">
          <button onClick={() => decide.mutate({ ids: members.map((m) => m.id), d: "clear" })}>Clear decisions</button>
          <button className="primary" onClick={() => auto.mutate()} title="Keep the highest-quality copy, mark the rest discard">
            Keep best, discard rest
          </button>
        </div>
      </div>
      {g.tier === "text" && (
        <div className="callout warn" style={{ marginBottom: 12 }}>
          These images share the same text but may be different pictures (e.g. the same caption on another photo). Compare before discarding.
        </div>
      )}
      {g.tier === "similar" && (
        <div className="callout warn" style={{ marginBottom: 12 }}>
          “Similar” groups are matched by meaning (CLIP) only and may be different photos of the same scene. Review carefully.
        </div>
      )}

      <div className="compare">
        {members.map((m) => (
          <div key={m.id} className={`card member ${m.decision ?? ""}`}>
            <div className="big" onClick={() => setZoom(`/api/photos/${m.id}/preview`)}>
              <img src={`/api/photos/${m.id}/preview?size=800`} alt={m.name} loading="lazy" />
            </div>
            <div className="row">
              {m.is_rep && <span className="badge good">★ best quality</span>}
              <Link to={`/photo/${m.id}`} className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>{m.rel_path}</Link>
            </div>
            <ScoreBadges p={m.is_rep ? null : m.vs_rep} />
            {m.ocr_text && (
              <div className="ocr-text small" title="Text read from the image (OCR)">
                {m.ocr_text}
              </div>
            )}
            <dl className="kv">
              {FIELDS.map((f) => (
                <FieldRow key={f.label} label={f.label} value={f.get(m)} diff={differs(f)} />
              ))}
            </dl>
            <div className="row" style={{ marginTop: "auto" }}>
              <button
                className={`grow ${m.decision === "keep" ? "on-keep" : ""}`}
                onClick={() => decide.mutate({ ids: [m.id], d: m.decision === "keep" ? "clear" : "keep" })}
              >
                ✓ Keep
              </button>
              <button
                className={`grow ${m.decision === "discard" ? "on-discard" : ""}`}
                onClick={() => decide.mutate({ ids: [m.id], d: m.decision === "discard" ? "clear" : "discard" })}
              >
                ✕ Discard
              </button>
            </div>
          </div>
        ))}
      </div>
      {zoom && <Lightbox src={zoom} onClose={() => setZoom(null)} />}
    </div>
  );
}

function FieldRow({ label, value, diff }: { label: string; value: string; diff: boolean }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className={diff ? "diff" : ""}>{value}</dd>
    </>
  );
}
