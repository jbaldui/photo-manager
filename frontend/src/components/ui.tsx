import { ReactNode, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import type { KeyCount, Pair, Photo } from "../api";
import { bytes, num } from "../format";

export function Tile({ label, value, hint, to }: { label: string; value: ReactNode; hint?: ReactNode; to?: string }) {
  const nav = useNavigate();
  return (
    <div
      className="tile"
      role={to ? "link" : undefined}
      tabIndex={to ? 0 : undefined}
      style={to ? { cursor: "pointer" } : undefined}
      onClick={to ? () => nav(to) : undefined}
      onKeyDown={to ? (e) => e.key === "Enter" && nav(to) : undefined}
    >
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

/** Horizontal bar list: single series, value labelled at the tip, hover tooltip. */
export function BarList({
  data,
  label = (k) => k,
  value = (d) => num(d.count),
  onPick,
  max = 12,
}: {
  data: KeyCount[];
  label?: (key: string) => string;
  value?: (d: KeyCount) => string;
  onPick?: (key: string) => void;
  max?: number;
}) {
  const shown = data.slice(0, max);
  const rest = data.slice(max);
  const rows = rest.length ? [...shown, { key: `Other (${rest.length})`, count: rest.reduce((s, d) => s + d.count, 0) }] : shown;
  const top = Math.max(1, ...rows.map((d) => d.count));
  const total = data.reduce((s, d) => s + d.count, 0);
  if (!rows.length) return <p className="sub">No data yet.</p>;
  return (
    <div className="bars" role="list">
      {rows.map((d) => {
        const isOther = d.key.startsWith("Other (") && rest.length > 0;
        return (
          <div
            key={d.key}
            role="listitem"
            className={`bar-row ${onPick && !isOther ? "link" : ""}`}
            onClick={onPick && !isOther ? () => onPick(d.key) : undefined}
            aria-label={`${label(d.key)}: ${value(d)}`}
          >
            <span className="bar-key" title={label(d.key)}>{label(d.key)}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${(d.count / top) * 80}%` }} />
              <span className="bar-val">{value(d)}</span>
            </span>
            <span className="tip">
              {label(d.key)} · {num(d.count)} ({total ? Math.round((d.count / total) * 100) : 0}%)
              {d.bytes ? ` · ${bytes(d.bytes)}` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** Column chart for year distributions. */
export function Columns({ data, onPick }: { data: KeyCount[]; onPick?: (key: string) => void }) {
  const top = Math.max(1, ...data.map((d) => d.count));
  if (!data.length) return <p className="sub">No data yet.</p>;
  const every = Math.ceil(data.length / 12);
  return (
    <div>
      <div className="cols" role="list">
        {data.map((d) => (
          <div
            key={d.key}
            role="listitem"
            aria-label={`${d.key}: ${num(d.count)}`}
            className={`col ${d.key === "unknown" ? "unknown" : ""}`}
            onClick={onPick ? () => onPick(d.key) : undefined}
          >
            <div className="col-fill" style={{ height: `${(d.count / top) * 100}%` }} />
            <span className="tip">
              {d.key === "unknown" ? "No date" : d.key} · {num(d.count)}
            </span>
          </div>
        ))}
      </div>
      <div className="col-axis">
        {data.map((d, i) => (
          <span key={d.key}>{i % every === 0 ? (d.key === "unknown" ? "?" : `'${d.key.slice(2)}`) : ""}</span>
        ))}
      </div>
    </div>
  );
}

export function Thumb({
  photo,
  onClick,
  badge,
  caption,
}: {
  photo: Photo;
  onClick?: () => void;
  badge?: ReactNode;
  caption?: ReactNode;
}) {
  return (
    <div
      className={`ph ${photo.decision ?? ""}`}
      onClick={onClick}
      title={photo.rel_path}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onClick?.()}
    >
      {photo.thumb_url ? <img src={photo.thumb_url} loading="lazy" alt={photo.name} /> : <div className="noimg">{photo.ext}</div>}
      {badge && <div className="corner">{badge}</div>}
      {caption !== null && <div className="cap">{caption ?? photo.name}</div>}
    </div>
  );
}

export function DecisionBadge({ d }: { d?: string | null }) {
  if (d === "keep") return <span className="badge good">✓ keep</span>;
  if (d === "discard") return <span className="badge bad">✕ discard</span>;
  return null;
}

/** Per-method evidence for why two images were matched. */
export function ScoreBadges({ p }: { p: (Partial<Pair> & { exact?: boolean }) | null | undefined }) {
  if (!p) return <span className="badge">best copy</span>;
  if (p.exact) return <span className="badge good" title="SHA-256 identical">SHA-256 identical</span>;
  const b: ReactNode[] = [];
  if (p.phash_d != null) {
    const cls = p.phash_d <= 6 ? "good" : p.phash_d <= 12 ? "warn" : "";
    b.push(<span key="p" className={`badge ${cls}`} title="pHash Hamming distance (0 = identical, of 64 bits)">pHash Δ{p.phash_d}</span>);
  }
  if (p.dhash_d != null)
    b.push(<span key="d" className={`badge ${p.dhash_d <= 10 ? "good" : ""}`} title="dHash Hamming distance">dHash Δ{p.dhash_d}</span>);
  if (p.whash_d != null)
    b.push(<span key="w" className={`badge ${p.whash_d <= 10 ? "good" : ""}`} title="wHash (wavelet) Hamming distance">wHash Δ{p.whash_d}</span>);
  if (p.rot_d != null && p.rot_d <= 6 && (p.phash_d ?? 99) > 6)
    b.push(<span key="r" className="badge good" title="Matches when rotated or mirrored">rotated Δ{p.rot_d}</span>);
  if (p.trim_d != null && p.trim_d <= 6 && (p.phash_d ?? 99) > 6)
    b.push(<span key="t" className="badge good" title="Matches after removing caption bars / borders">caption/border Δ{p.trim_d}</span>);
  if (p.clip != null)
    b.push(
      <span key="c" className={`badge ${p.clip >= 0.95 ? "good" : p.clip >= 0.9 ? "accent" : ""}`} title="CLIP cosine similarity (semantic)">
        CLIP {p.clip.toFixed(3)}
      </span>,
    );
  if (p.text_sim != null)
    b.push(
      <span key="x" className={`badge ${p.text_sim >= 0.95 ? "good" : "accent"}`} title="OCR: share of words in common (Jaccard)">
        Text {Math.round(p.text_sim * 100)}% same
      </span>,
    );
  if (p.orb_inliers != null)
    b.push(
      <span key="o" className={`badge ${p.orb_inliers >= 25 ? "good" : "bad"}`} title="ORB keypoints consistent with one geometric transform (RANSAC inliers)">
        ORB {p.orb_inliers} pts
      </span>,
    );
  return <div className="scores">{b}</div>;
}

export function Modal({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ display: "contents" }}>
        {children}
      </div>
    </div>
  );
}

export function Lightbox({ src, onClose }: { src: string; onClose: () => void }) {
  return (
    <Modal onClose={onClose}>
      <div className="lightbox" onClick={onClose}>
        <img src={src} alt="" />
      </div>
    </Modal>
  );
}
