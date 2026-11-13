import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Generated } from "../api";
import BeforeAfter from "../components/BeforeAfter";
import FolderPicker from "../components/FolderPicker";
import { Modal } from "../components/ui";
import { bytes, epoch, num } from "../format";

export default function Enhanced() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["generated"], queryFn: () => api.generated() });
  const [view, setView] = useState<Generated | null>(null);
  const [saving, setSaving] = useState<Generated | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const del = useMutation({ mutationFn: api.deleteGenerated, onSuccess: () => qc.invalidateQueries({ queryKey: ["generated"] }) });
  const save = useMutation({
    mutationFn: (a: { id: number; dest: string }) => api.saveGenerated(a.id, a.dest),
    onSuccess: (r) => setMsg(`Saved a copy to ${r.path}`),
    onError: (e: Error) => setMsg(e.message),
  });

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Enhanced images</h1>
          <p className="sub">
            New images made by merging copies of a photo and upscaling them. Start one from a duplicate group (“Enhance”) or a photo page. They are stored in the
            app's data folder, separate from your library.
          </p>
        </div>
      </div>
      {msg && <div className="callout" style={{ marginBottom: 12, overflowWrap: "anywhere" }}>{msg}</div>}
      {!list.data?.length && !list.isLoading && (
        <div className="card empty">
          <h2>No enhanced images yet</h2>
          <p>Open a group in <Link to="/groups?tier=duplicate">Duplicates</Link> and press “Enhance”.</p>
        </div>
      )}
      <div className="grid-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
        {list.data?.map((g) => (
          <div key={g.id} className="card group-card">
            <div className="gen-thumb" onClick={() => setView(g)}>
              <img src={g.image_url} alt={g.name} loading="lazy" />
            </div>
            <div className="mono" style={{ fontSize: 12, overflowWrap: "anywhere" }}>{g.name}</div>
            <div className="sub" style={{ fontSize: 12 }}>
              {g.width} × {g.height} · {bytes(g.bytes)} · {g.params.fused ? `${num(g.source_file_ids.length)} copies merged` : "single photo"}
              {g.params.model !== "none" ? ` · ${g.params.model} upscaling` : ""} · {epoch(g.created)}
            </div>
            <div className="row">
              <button className="small" onClick={() => setView(g)}>Compare</button>
              <a className="btn small" href={`${g.image_url}?download=1`}>Download</a>
              <button className="small" onClick={() => setSaving(g)}>Save copy to…</button>
              {g.group_id && <Link className="btn small ghost" to={`/groups/${g.group_id}`}>Group</Link>}
              <span className="grow" />
              <button className="small danger" onClick={() => confirm("Delete this enhanced image? Your originals are not affected.") && del.mutate(g.id)}>Delete</button>
            </div>
          </div>
        ))}
      </div>
      {view && (
        <Modal onClose={() => setView(null)}>
          <div className="modal wide" role="dialog" aria-label="Compare">
            <header>
              <h2 className="grow" style={{ margin: 0 }}>{view.name}</h2>
              <button className="ghost" onClick={() => setView(null)} aria-label="Close">✕</button>
            </header>
            <div className="modal-body">
              <BeforeAfter before={`/api/photos/${view.source_file_ids[0]}/preview?size=1600`} after={view.image_url} />
              <table className="simple" style={{ marginTop: 10 }}>
                <tbody>
                  {view.params.sources.map((s) => (
                    <tr key={s.id}>
                      <td className="mono"><Link to={`/photo/${s.id}`}>{s.rel_path}</Link></td>
                      <td>{s.role}</td>
                      <td className="sub">{s.ecc != null ? `alignment ${s.ecc.toFixed(3)} · ${(100 * (s.coverage ?? 0)).toFixed(0)}% used` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Modal>
      )}
      {saving && (
        <FolderPicker
          title="Save a copy to…"
          note="Must be outside your scanned folders (otherwise it would show up as a new photo)."
          onClose={() => setSaving(null)}
          onPick={(p) => {
            save.mutate({ id: saving.id, dest: p });
            setSaving(null);
          }}
        />
      )}
    </div>
  );
}
