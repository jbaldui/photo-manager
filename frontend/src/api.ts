export type Decision = "keep" | "discard" | null;
export type Tier = "exact" | "duplicate" | "near" | "similar" | "text";

export interface Photo {
  id: number;
  root_id: number;
  rel_path: string;
  name: string;
  ext: string;
  size: number;
  mtime: number;
  status: string;
  sha256: string | null;
  format: string | null;
  width: number | null;
  height: number | null;
  date_taken: string | null;
  date_source: string | null;
  camera_make: string | null;
  camera_model: string | null;
  source_guess: string | null;
  has_exif: number | null;
  error: string | null;
  thumb_url: string | null;
  decision?: Decision;
  score?: number;
  copies?: number;
  snippet?: string | null;
}

export type OcrLine = [string, number, [number, number, number, number]];

export interface Pair {
  a: string;
  b: string;
  phash_d: number;
  dhash_d: number;
  whash_d: number;
  rot_d: number;
  trim_d: number;
  clip: number | null;
  orb_inliers: number | null;
  orb_ratio: number | null;
  tier: Tier | null;
  text_sim: number | null;
}

export interface GroupMember extends Photo {
  is_rep: boolean;
  vs_rep: (Partial<Pair> & { exact?: boolean }) | null;
  lens?: string | null;
  software?: string | null;
  gps_lat?: number | null;
  gps_lon?: number | null;
  ocr_text?: string | null;
}

export interface Group {
  id: number;
  tier: Tier;
  size: number;
  rep_file_id: number;
  total_bytes: number;
  reclaimable_bytes: number;
  decided?: number;
  preview?: Photo[];
  members?: GroupMember[];
}

export interface StageState {
  label: string;
  status: "queued" | "running" | "done" | "skipped" | "cancelled" | "error";
  processed: number;
  total: number | null;
  message: string;
  rate: number | null;
  eta_seconds: number | null;
  result: Record<string, number> | null;
}

export interface ScanStatus {
  running: boolean;
  current: string | null;
  error: string | null;
  started: number | null;
  finished: number | null;
  stages: Record<string, StageState>;
}

export interface Root {
  id: number;
  path: string;
  files: number;
  ok: number | null;
  pending: number | null;
  errors: number | null;
  missing: number | null;
  exists: boolean;
}

export interface KeyCount {
  key: string;
  count: number;
  bytes?: number;
  model?: string;
}

export interface Stats {
  total_files: number;
  total_bytes: number;
  unique_images: number;
  errors: number;
  pending: number;
  missing: number;
  removed: number;
  removed_bytes: number;
  embedded: number;
  images: number;
  ocr_done: number;
  with_text: number;
  with_exif: number;
  with_date: number;
  with_gps: number;
  formats: KeyCount[];
  years: KeyCount[];
  mtime_years: KeyCount[];
  date_sources: KeyCount[];
  cameras: KeyCount[];
  sources: KeyCount[];
  tiers: Partial<Record<Tier, { groups: number; files: number; reclaimable_bytes: number }>>;
  decisions: KeyCount[];
}

export interface PhotoDetail extends Photo {
  root_path: string;
  abs_path: string;
  lens: string | null;
  software: string | null;
  gps_lat: number | null;
  gps_lon: number | null;
  orientation: number | null;
  ctime: number | null;
  mode: string | null;
  frames: number | null;
  has_alpha: number | null;
  exif: Record<string, unknown> | null;
  groups: { id: number; tier: Tier; size: number }[];
  same_content: { id: number; rel_path: string; mtime: number; size: number }[];
  ocr: { text: string; lines: OcrLine[]; n_words: number; error: string | null } | null;
  hashes?: { sha256: string; phash: string; dhash: string; whash: string; colorhash: string; clip_embedded: boolean };
}

export interface OrganizeOptions {
  dest: string;
  scope: "all" | "grouped" | "not_discarded";
  layout: "flat" | "groups";
  tiers: Tier[];
  one_per_exact: boolean;
  rename: "keep" | "duplicates" | "all";
  pattern: "date" | "date_name" | "group";
}

export interface OrganizePreview {
  files: number;
  bytes: number;
  folders: number;
  groups: number;
  renamed: number;
  free_bytes: number;
  sample: { file_id: number; src: string; dst: string; group: number | null; is_best: boolean; renamed: boolean }[];
}

export interface OrganizeStatus {
  running: boolean;
  out?: string;
  total?: number;
  done?: number;
  bytes_total?: number;
  bytes_done?: number;
  errors?: { src: string; error: string }[];
  started?: number;
  finished?: number | null;
  cancelled?: boolean;
}

export interface RemovalOptions {
  dest?: string | null;
  scope?: "discarded" | "files";
  file_ids?: number[];
  group_id?: number | null;
}

export interface RemovalPreview {
  files: number;
  bytes: number;
  blocked: { file_id: number; rel_path: string; reason: string }[];
  dest: string;
  dest_exists: boolean;
  same_device: boolean;
  free_bytes: number;
  sample: { file_id: number; src: string; qrel: string; size: number }[];
}

export interface JobStatus {
  running: boolean;
  mode?: "move" | "restore";
  batch_id?: number;
  phase?: string;
  total?: number;
  done?: number;
  moved?: number;
  skipped?: number;
  conflicts?: number;
  bytes_total?: number;
  bytes_done?: number;
  errors?: { src: string; error: string }[];
  started?: number;
  finished?: number | null;
  cancelled?: boolean;
  dest?: string;
}

export interface RemovalBatch {
  id: number;
  created: number;
  dest: string;
  reason: string;
  files: number;
  bytes: number;
  state: string;
  restored_at: number | null;
  counts: Record<string, number>;
  in_quarantine: number;
  items?: { id: number; file_id: number; root_path: string; rel_path: string; quarantine_path: string; size: number; status: string; error: string | null }[];
}

export interface PipelineConfig {
  scan: { mode: "refresh" | "full" };
  decide: { enabled: boolean; tiers: Tier[] };
  organize: { enabled: boolean; options: Partial<OrganizeOptions> };
  quarantine: { enabled: boolean; dest: string | null; include_manual_discards: boolean };
}

export interface AutoStep {
  key: "scan" | "decide" | "organize" | "quarantine";
  label: string;
  status: "queued" | "running" | "done" | "skipped" | "cancelled" | "error" | "not run";
  message: string;
  result: Record<string, number | string> | null;
}

export interface AutoStatus {
  running: boolean;
  pipeline_id?: number;
  name?: string;
  dry_run?: boolean;
  error?: string | null;
  started?: number;
  finished?: number | null;
  cancelled?: boolean;
  steps?: AutoStep[];
}

export interface Pipeline {
  id: number;
  name: string;
  builtin: number;
  config: PipelineConfig;
  last_run: number | null;
  last_result: AutoStatus | null;
}

export interface EnhanceOptions {
  group_id?: number | null;
  file_ids?: number[];
  fuse: boolean;
  model: "fast" | "quality" | "none";
  scale: 1 | 2 | 4;
  format?: "auto" | "jpg" | "png";
}

export interface EnhanceEstimate {
  sources: number;
  fuse: boolean;
  tier: Tier | null;
  reference: { id: number; rel_path: string; width: number; height: number };
  output: { width: number; height: number };
  super_resolution: boolean;
  model_downloaded: boolean;
  seconds: number;
  notes: string[];
}

export interface Generated {
  id: number;
  group_id: number | null;
  source_file_ids: number[];
  path: string;
  name: string;
  created: number;
  width: number;
  height: number;
  bytes: number | null;
  image_url: string;
  params: EnhanceOptions & { sources: { id: number; rel_path: string; role: string; ecc?: number; coverage?: number }[]; fused: boolean; tier: Tier | null };
}

export interface EnhanceStatus {
  running: boolean;
  phase?: string;
  progress?: number;
  message?: string;
  error?: string | null;
  result?: Generated | null;
  started?: number;
  finished?: number | null;
  estimate?: EnhanceEstimate;
  cancelled?: boolean;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const qs = (p: Record<string, unknown>) => {
  const s = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  });
  return s.toString();
};

export const api = {
  browse: (path?: string) =>
    req<{ path: string; parent: string | null; dirs: { name: string; path: string }[]; shortcuts: { name: string; path: string }[] }>(
      "GET",
      `/api/fs/browse?${qs({ path })}`,
    ),
  roots: () => req<Root[]>("GET", "/api/roots"),
  addRoot: (path: string) => req<{ id: number }>("POST", "/api/roots", { path }),
  removeRoot: (id: number) =>
    req<{ ok: boolean; removed: { files: number; images: number; thumbnails: number; groups: Record<string, number> } }>(
      "DELETE",
      `/api/roots/${id}`,
    ),
  startScan: (stages?: string[]) => req<ScanStatus>("POST", "/api/scan", { stages }),
  cancelScan: () => req<ScanStatus>("POST", "/api/scan/cancel"),
  scanStatus: () => req<ScanStatus>("GET", "/api/scan/status"),
  stats: () => req<Stats>("GET", "/api/stats"),
  photos: (p: Record<string, unknown>) => req<{ total: number; offset: number; items: Photo[] }>("GET", `/api/photos?${qs(p)}`),
  photo: (id: number) => req<PhotoDetail>("GET", `/api/photos/${id}`),
  similar: (id: number) => req<{ items: (Photo & { pair: Pair | null })[] }>("GET", `/api/photos/${id}/similar`),
  search: (q: string, mode: "text" | "clip") => req<{ items: Photo[]; total?: number }>("GET", `/api/search?${qs({ q, mode })}`),
  groups: (p: Record<string, unknown>) => req<{ total: number; items: Group[] }>("GET", `/api/groups?${qs(p)}`),
  group: (id: number) => req<Group & { pairs: Pair[] }>("GET", `/api/groups/${id}`),
  decide: (file_ids: number[], decision: "keep" | "discard" | "clear") => req("PUT", "/api/decisions", { file_ids, decision }),
  autoDecide: (groupId: number) => req("POST", `/api/groups/${groupId}/auto-decide`),
  autoDecideTier: (tier: Tier) => req<{ groups_decided: number }>("POST", "/api/groups/auto-decide-tier", { tier }),
  settings: () =>
    req<{ values: Record<string, number>; defaults: Record<string, number>; data_dir: string; workers: number }>("GET", "/api/settings"),
  putSettings: (values: Record<string, number>) => req("PUT", "/api/settings", values),
  exportData: (kind: "csv" | "json") => req<{ path: string; name: string; url: string }>("POST", "/api/export", { kind }),
  organizePreview: (o: OrganizeOptions) => req<OrganizePreview>("POST", "/api/organize/preview", o),
  organizeStart: (o: OrganizeOptions) => req<OrganizeStatus>("POST", "/api/organize/start", o),
  organizeStatus: () => req<OrganizeStatus>("GET", "/api/organize/status"),
  organizeCancel: () => req<OrganizeStatus>("POST", "/api/organize/cancel"),
  removalDefaults: () => req<{ dest: string }>("GET", "/api/removals/defaults"),
  removalPreview: (o: RemovalOptions) => req<RemovalPreview>("POST", "/api/removals/preview", o),
  removalStart: (o: RemovalOptions) => req<JobStatus>("POST", "/api/removals/start", o),
  removalStatus: () => req<JobStatus>("GET", "/api/removals/status"),
  removalCancel: () => req<JobStatus>("POST", "/api/removals/cancel"),
  removalBatches: () => req<RemovalBatch[]>("GET", "/api/removals"),
  removalBatch: (id: number) => req<RemovalBatch>("GET", `/api/removals/${id}`),
  restoreBatch: (id: number) => req<JobStatus>("POST", `/api/removals/${id}/restore`),
  pipelines: () => req<Pipeline[]>("GET", "/api/pipelines"),
  createPipeline: (name: string, config: PipelineConfig) => req<Pipeline>("POST", "/api/pipelines", { name, config }),
  updatePipeline: (id: number, name: string, config: PipelineConfig) => req<Pipeline>("PUT", `/api/pipelines/${id}`, { name, config }),
  deletePipeline: (id: number) => req("DELETE", `/api/pipelines/${id}`),
  runPipeline: (id: number, dry_run: boolean) => req<AutoStatus>("POST", `/api/pipelines/${id}/run`, { dry_run }),
  autoStatus: () => req<AutoStatus>("GET", "/api/auto/status"),
  autoCancel: () => req<AutoStatus>("POST", "/api/auto/cancel"),
  enhanceEstimate: (o: EnhanceOptions) => req<EnhanceEstimate>("POST", "/api/enhance/estimate", o),
  enhanceStart: (o: EnhanceOptions) => req<EnhanceStatus>("POST", "/api/enhance", o),
  enhanceStatus: () => req<EnhanceStatus>("GET", "/api/enhance/status"),
  enhanceCancel: () => req<EnhanceStatus>("POST", "/api/enhance/cancel"),
  generated: (group_id?: number) => req<Generated[]>("GET", `/api/generated?${qs({ group_id })}`),
  deleteGenerated: (id: number) => req("DELETE", `/api/generated/${id}`),
  saveGenerated: (id: number, dest: string) => req<{ path: string }>("POST", `/api/generated/${id}/save`, { dest }),
  errors: () => req<{ id: number; root: string; rel_path: string; size: number; error: string }[]>("GET", "/api/errors"),
};
