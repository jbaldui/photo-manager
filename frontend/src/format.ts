export function bytes(n: number | null | undefined): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export const num = (n: number | null | undefined) => (n ?? 0).toLocaleString();

export const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "–");

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function epoch(s: number | null | undefined): string {
  if (!s) return "–";
  return new Date(s * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function duration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "–";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
  return `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`;
}

export const DATE_SOURCE_LABEL: Record<string, string> = {
  exif_original: "EXIF (taken)",
  exif_digitized: "EXIF (digitized)",
  exif_modified: "EXIF (modified)",
  filename: "From filename",
  none: "No date found",
};

export const TIER_INFO: Record<string, { label: string; help: string }> = {
  exact: { label: "Exact copies", help: "Byte-identical files (same SHA-256), even if names or dates differ." },
  duplicate: {
    label: "Duplicates",
    help: "Same picture re-saved, resized, recompressed, converted (JPG↔PNG…) or rotated. Found by perceptual hashes, confirmed by CLIP + ORB.",
  },
  near: {
    label: "Edited versions",
    help: "Crops, captions/meme text, borders, filters or watermarks. Geometrically verified with ORB keypoint matching.",
  },
  similar: {
    label: "Similar",
    help: "Visually or semantically alike (same scene, burst shots). CLIP only, not verified — review before discarding.",
  },
  text: {
    label: "Same text",
    help: "Different pictures carrying the same words (meme captions, forwarded messages, documents), read with OCR. Not duplicates — review before discarding.",
  },
};
