import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, AutoStatus, OrganizeOptions, Pipeline, PipelineConfig, Tier } from "../api";
import { DEFAULT_ORGANIZE, FolderField, OrganizeFields } from "../components/OrganizeForm";
import { bytes, epoch, num, TIER_INFO } from "../format";

const STEP_HELP: Record<string, string> = {
  scan: "Finds new, moved and deleted files and updates the groups.",
  decide: "In every undecided group: keep the best copy, mark the rest discard.",
  organize: "Writes an organized copy of every photo not marked discard.",
  quarantine: "Moves the copies discarded by this run to quarantine (undoable).",
};

function Toggle({ on, onChange, title, help }: { on: boolean; onChange: (v: boolean) => void; title: string; help: string }) {
  return (
    <label className="row" style={{ alignItems: "flex-start", cursor: "pointer" }}>
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 3 }} />
      <span>
        <strong>{title}</strong>
        <span className="sub" style={{ display: "block", fontSize: 12 }}>{help}</span>
      </span>
    </label>
  );
}

export default function AutoProcess() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["pipelines"], queryFn: api.pipelines });
  const status = useQuery({ queryKey: ["auto-status"], queryFn: api.autoStatus, refetchInterval: (q) => (q.state.data?.running ? 700 : 4000) });
  const [sel, setSel] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [cfg, setCfg] = useState<PipelineConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pipelines = list.data ?? [];
  const current = pipelines.find((p) => p.id === sel) ?? null;

  useEffect(() => {
    if (sel === null && pipelines.length) setSel(pipelines[0].id);
  }, [pipelines, sel]);
  useEffect(() => {
    if (current && !dirty) {
      setName(current.name);
      setCfg(current.config);
    }
  }, [current, dirty]);

  const edit = (fn: (c: PipelineConfig) => PipelineConfig) => {
    setCfg((c) => (c ? fn(structuredClone(c)) : c));
    setDirty(true);
  };
  const org: OrganizeOptions = { ...DEFAULT_ORGANIZE, ...(cfg?.organize.options ?? {}), scope: "not_discarded" };
  const setOrg = <K extends keyof OrganizeOptions>(k: K, v: OrganizeOptions[K]) =>
    edit((c) => ({ ...c, organize: { ...c.organize, options: { ...org, [k]: v } } }));

  const saveM = useMutation({
    mutationFn: async (asNew: boolean) => {
      if (!cfg) throw new Error("nothing to save");
      return asNew || !current ? api.createPipeline(name || "My pipeline", cfg) : api.updatePipeline(current.id, name, cfg);
    },
    onSuccess: (p: Pipeline) => {
      setDirty(false);
      setSel(p.id);
      setErr(null);
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
    onError: (e: Error) => setErr(e.message),
  });
  const delM = useMutation({
    mutationFn: (id: number) => api.deletePipeline(id),
    onSuccess: () => {
      setSel(null);
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
  });
  const runM = useMutation({
    mutationFn: async (dry: boolean) => {
      if (!current || !cfg) throw new Error("Choose a pipeline");
      if (dirty) await api.updatePipeline(current.id, name, cfg);
      return api.runPipeline(current.id, dry);
    },
    onSuccess: (s) => {
      setDirty(false);
      setErr(null);
      qc.setQueryData(["auto-status"], s);
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      qc.invalidateQueries({ queryKey: ["auto-status"] });
    },
    onError: (e: Error) => setErr(e.message),
  });

  const st = status.data;
  const running = !!st?.running;
  const confirmRun = () => {
    if (!cfg) return;
    const parts = [
      "• refresh the library",
      cfg.decide.enabled && `• keep the best copy of every undecided ${cfg.decide.tiers.map((t) => TIER_INFO[t].label.toLowerCase()).join(" / ")} group`,
      cfg.organize.enabled && `• copy your photos into ${org.dest || "(no destination!)"}`,
      cfg.quarantine.enabled && "• MOVE the discarded copies to quarantine (undo from the Quarantine page)",
    ].filter(Boolean);
    if (confirm(`Run “${name}” now? It will:\n\n${parts.join("\n")}\n\nTip: a dry run first shows the numbers without changing anything.`)) runM.mutate(false);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Auto Process</h1>
          <p className="sub">
            Saved pipelines that clean up and organize your library in one go. Only <strong>exact</strong> and <strong>duplicate</strong> groups are decided
            automatically; edited, similar and same-text groups always wait for you. Every move can be undone.
          </p>
        </div>
      </div>
      {err && <div className="callout error" style={{ marginBottom: 12 }}>{err}</div>}

      {st?.steps && (st.running || st.finished) && <RunReport st={st} />}

      <div className="auto-layout">
        <div className="stack">
          {pipelines.map((p) => (
            <button
              key={p.id}
              className={`pipe-card ${p.id === sel ? "on" : ""}`}
              onClick={() => {
                if (dirty && !confirm("Discard unsaved changes?")) return;
                setDirty(false);
                setSel(p.id);
              }}
            >
              <strong>{p.name}</strong>
              <span className="sub" style={{ fontSize: 12 }}>
                {[
                  "refresh",
                  p.config.decide.enabled && "decide",
                  p.config.organize.enabled && "organize",
                  p.config.quarantine.enabled && "quarantine",
                ].filter(Boolean).join(" → ")}
              </span>
              <span className="sub" style={{ fontSize: 11 }}>{p.last_run ? `last run ${epoch(p.last_run)}` : p.builtin ? "preset" : "never run"}</span>
            </button>
          ))}
        </div>

        {cfg && (
          <div className="stack">
            <div className="card">
              <div className="row">
                <input className="grow" value={name} onChange={(e) => (setName(e.target.value), setDirty(true))} aria-label="Pipeline name" />
                <button onClick={() => saveM.mutate(false)} disabled={!dirty}>Save</button>
                <button onClick={() => saveM.mutate(true)}>Save as new</button>
                {current && !current.builtin && (
                  <button className="danger" onClick={() => confirm(`Delete “${current.name}”?`) && delM.mutate(current.id)}>Delete</button>
                )}
              </div>
            </div>

            <div className="card stack">
              <div>
                <h2><span className="step">1</span>Refresh library</h2>
                <div className="seg">
                  {(["refresh", "full"] as const).map((m) => (
                    <button key={m} className={cfg.scan.mode === m ? "active" : ""} onClick={() => edit((c) => ({ ...c, scan: { mode: m } }))}>
                      {m === "refresh" ? "Quick check (seconds)" : "Full analysis of new photos"}
                    </button>
                  ))}
                </div>
                <p className="sub" style={{ fontSize: 12, marginTop: 6 }}>
                  {cfg.scan.mode === "refresh"
                    ? "Finds moved/deleted files. New photos are found but not analysed: run a full scan for those."
                    : "Runs every analysis stage on new or changed photos (CLIP and OCR can take a while)."}
                </p>
              </div>

              <div>
                <h2><span className="step">2</span>Decide</h2>
                <Toggle on={cfg.decide.enabled} onChange={(v) => edit((c) => ({ ...c, decide: { ...c.decide, enabled: v } }))} title="Keep the best copy, discard the rest" help={STEP_HELP.decide} />
                {cfg.decide.enabled && (
                  <div className="row" style={{ marginTop: 8, marginLeft: 24 }}>
                    {(["exact", "duplicate"] as Tier[]).map((t) => (
                      <label key={t} className={`badge ${cfg.decide.tiers.includes(t) ? "accent" : ""}`} style={{ cursor: "pointer", padding: "5px 10px", fontSize: 12 }}>
                        <input
                          type="checkbox"
                          style={{ margin: "0 4px 0 0" }}
                          checked={cfg.decide.tiers.includes(t)}
                          onChange={(e) =>
                            edit((c) => ({ ...c, decide: { ...c.decide, tiers: e.target.checked ? [...c.decide.tiers, t] : c.decide.tiers.filter((x) => x !== t) } }))
                          }
                        />
                        {TIER_INFO[t].label}
                      </label>
                    ))}
                    <span className="sub" style={{ fontSize: 12 }}>Groups you already decided are left alone.</span>
                  </div>
                )}
              </div>

              <div>
                <h2><span className="step">3</span>Organize (copy)</h2>
                <Toggle on={cfg.organize.enabled} onChange={(v) => edit((c) => ({ ...c, organize: { ...c.organize, enabled: v } }))} title="Create an organized copy" help={STEP_HELP.organize} />
              </div>
            </div>
            {cfg.organize.enabled && <OrganizeFields o={org} set={setOrg} numbered={false} />}

            <div className="card stack">
              <div>
                <h2><span className="step">4</span>Quarantine</h2>
                <Toggle on={cfg.quarantine.enabled} onChange={(v) => edit((c) => ({ ...c, quarantine: { ...c.quarantine, enabled: v } }))} title="Move discarded copies to quarantine" help={STEP_HELP.quarantine} />
              </div>
              {cfg.quarantine.enabled && (
                <>
                  <FolderField
                    value={cfg.quarantine.dest ?? ""}
                    onChange={(p) => edit((c) => ({ ...c, quarantine: { ...c.quarantine, dest: p || null } }))}
                    placeholder="Default: ~/Photo Manager Quarantine (or the last folder you used)"
                    title="Choose the quarantine folder"
                    note="A dated sub-folder is created for each run."
                  />
                  <Toggle
                    on={cfg.quarantine.include_manual_discards}
                    onChange={(v) => edit((c) => ({ ...c, quarantine: { ...c.quarantine, include_manual_discards: v } }))}
                    title="Also move photos I marked discard myself"
                    help="Off: only what this run decided is moved."
                  />
                </>
              )}
            </div>

            <div className="row" style={{ justifyContent: "flex-end" }}>
              {running ? (
                <button className="danger" onClick={() => api.autoCancel().then(() => status.refetch())}>Stop</button>
              ) : (
                <>
                  <button disabled={runM.isPending} onClick={() => runM.mutate(true)}>Dry run (changes nothing)</button>
                  <button className="primary" disabled={runM.isPending} onClick={confirmRun}>Run now</button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function RunReport({ st }: { st: AutoStatus }) {
  const q = st.steps?.find((s) => s.key === "quarantine")?.result;
  const o = st.steps?.find((s) => s.key === "organize")?.result;
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ marginBottom: 4 }}>
        <h2 className="grow" style={{ margin: 0 }}>
          {st.name} {st.dry_run && <span className="badge">dry run</span>} —{" "}
          {st.running ? "running…" : st.error ? "failed" : st.cancelled ? "stopped" : st.dry_run ? "this is what would happen" : "done"}
        </h2>
        {st.finished && <span className="sub" style={{ fontSize: 12 }}>{epoch(st.finished)}</span>}
      </div>
      {st.error && <div className="callout error" style={{ marginBottom: 8 }}>{st.error}</div>}
      {st.steps?.map((s) => (
        <div className="stage" key={s.key}>
          <span className={`dot ${s.status === "not run" ? "skipped" : s.status}`} aria-label={s.status} />
          <div>
            <div style={{ fontWeight: 600 }}>{s.label}</div>
            <div className="sub" style={{ fontSize: 12 }}>{s.message || STEP_HELP[s.key]}</div>
          </div>
          <div>{s.status !== "running" && <span className="badge">{s.status}</span>}</div>
        </div>
      ))}
      {!st.running && !st.dry_run && !st.error && (
        <div className="row" style={{ marginTop: 10 }}>
          {q && Number(q.files) > 0 && (
            <Link className="btn" to="/quarantine">{num(Number(q.files))} photos ({bytes(Number(q.bytes))}) in quarantine: review or undo</Link>
          )}
          {o?.out && <span className="mono sub" style={{ fontSize: 12, overflowWrap: "anywhere" }}>Organized copy: {String(o.out)}</span>}
          <Link className="btn ghost" to="/groups">See remaining groups</Link>
        </div>
      )}
    </div>
  );
}
