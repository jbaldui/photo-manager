import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Modal } from "./ui";

/** Server-side folder browser: browsers can't reveal absolute paths, so the backend lists directories. */
export default function FolderPicker({
  onPick,
  onClose,
  title = "Choose your photos folder",
  note = "Sub-folders are included.",
}: {
  onPick: (path: string) => void;
  onClose: () => void;
  title?: string;
  note?: string;
}) {
  const [path, setPath] = useState<string | undefined>(undefined);
  const [typed, setTyped] = useState("");
  const { data, error, isLoading } = useQuery({ queryKey: ["browse", path], queryFn: () => api.browse(path) });

  const parts = data?.path.split("/").filter(Boolean) ?? [];

  return (
    <Modal onClose={onClose}>
      <div className="modal" role="dialog" aria-label="Choose a folder">
        <header style={{ flexDirection: "column", alignItems: "stretch" }}>
          <div className="row">
            <h2 style={{ margin: 0 }} className="grow">{title}</h2>
            <button className="ghost" onClick={onClose} aria-label="Close">✕</button>
          </div>
          <div className="row">
            {data?.shortcuts.map((s) => (
              <button key={s.path} className="small" onClick={() => setPath(s.path)}>
                {s.name}
              </button>
            ))}
          </div>
          <form
            className="row"
            onSubmit={(e) => {
              e.preventDefault();
              if (typed.trim()) setPath(typed.trim());
            }}
          >
            <input className="grow" placeholder="…or type a path, e.g. /media/me/disk/photos" value={typed} onChange={(e) => setTyped(e.target.value)} />
            <button type="submit">Go</button>
          </form>
          <div className="crumbs">
            <button onClick={() => setPath("/")}>/</button>
            {parts.map((p, i) => (
              <span key={i}>
                <button onClick={() => setPath("/" + parts.slice(0, i + 1).join("/"))}>{p}</button>/
              </span>
            ))}
          </div>
        </header>
        <div className="body">
          {isLoading && <p className="sub" style={{ padding: 8 }}>Loading…</p>}
          {error && <div className="callout error">{(error as Error).message}</div>}
          {data?.parent && (
            <div className="dir" onClick={() => setPath(data.parent!)}>
              <span aria-hidden>↰</span> ..
            </div>
          )}
          {data?.dirs.map((d) => (
            <div key={d.path} className="dir" onClick={() => setPath(d.path)} onDoubleClick={() => onPick(d.path)}>
              <span aria-hidden>📁</span> {d.name}
            </div>
          ))}
          {data && !data.dirs.length && <p className="sub" style={{ padding: 8 }}>No sub-folders.</p>}
        </div>
        <footer>
          <span className="grow mono" style={{ color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis" }}>
            {data?.path}
          </span>
          <span className="sub" style={{ fontSize: 12 }}>{note}</span>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!data} onClick={() => data && onPick(data.path)}>
            Use this folder
          </button>
        </footer>
      </div>
    </Modal>
  );
}
