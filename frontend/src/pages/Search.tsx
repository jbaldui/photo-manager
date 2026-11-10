import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Thumb } from "../components/ui";
import { num } from "../format";

type Mode = "text" | "clip";

const EXAMPLES: Record<Mode, string[]> = {
  text: ["bom dia", "\"happy birthday\"", "total", "whatsapp", "meeting", "obrigado"],
  clip: ["sunset at the beach", "a dog", "birthday cake", "screenshot of a chat", "document or receipt", "snow mountains"],
};

const MODE_INFO: Record<Mode, { label: string; help: string; placeholder: string }> = {
  text: {
    label: "Text in image",
    help: "Finds the words written inside photos (OCR): memes, screenshots, signs, documents. Accents and case are ignored; use \"quotes\" for an exact phrase.",
    placeholder: "e.g. reunião amanhã, \"when the code works\"",
  },
  clip: {
    label: "Visual content",
    help: "Describe what the photo shows. Uses the local CLIP model.",
    placeholder: "e.g. people smiling at a party",
  },
};

/** Renders FTS snippets: [[match]] -> highlighted. */
export function Snippet({ text }: { text: string }) {
  const parts = text.split(/(\[\[.*?\]\])/g);
  return (
    <span className="snippet">
      {parts.map((p, i) => (p.startsWith("[[") ? <mark key={i}>{p.slice(2, -2)}</mark> : <span key={i}>{p}</span>))}
    </span>
  );
}

export default function Search() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const mode = (sp.get("mode") as Mode) || "text";
  const [text, setText] = useState(q);
  const res = useQuery({ queryKey: ["search", mode, q], queryFn: () => api.search(q, mode), enabled: !!q });

  const run = (v: string, m: Mode = mode) => {
    setText(v);
    setSp(v ? { q: v, mode: m } : { mode: m }, { replace: true });
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Search</h1>
          <p className="sub">{MODE_INFO[mode].help}</p>
        </div>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <div className="seg" role="tablist">
          {(["text", "clip"] as Mode[]).map((m) => (
            <button key={m} role="tab" aria-selected={m === mode} className={m === mode ? "active" : ""} onClick={() => run(text.trim(), m)}>
              {MODE_INFO[m].label}
            </button>
          ))}
        </div>
      </div>
      <form
        className="row"
        style={{ marginBottom: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          run(text.trim());
        }}
      >
        <input className="grow" style={{ maxWidth: 560 }} value={text} onChange={(e) => setText(e.target.value)} placeholder={MODE_INFO[mode].placeholder} autoFocus />
        <button className="primary" type="submit">Search</button>
      </form>
      <div className="row" style={{ marginBottom: 16 }}>
        {EXAMPLES[mode].map((x) => (
          <button key={x} className="small" onClick={() => run(x)}>{x}</button>
        ))}
      </div>
      {res.isFetching && <p className="sub">Searching…</p>}
      {res.error && <div className="callout warn">{(res.error as Error).message}</div>}
      {res.data && (
        <>
          <p className="sub" style={{ marginBottom: 8 }}>
            {mode === "text"
              ? `${num(res.data.total ?? res.data.items.length)} ${(res.data.total ?? res.data.items.length) === 1 ? "image contains" : "images contain"} this text${(res.data.total ?? 0) > res.data.items.length ? ` (showing best ${res.data.items.length})` : ""}`
              : `Best ${res.data.items.length} visual matches`}
          </p>
          {!res.data.items.length && <div className="card empty"><h2>No matches</h2><p>Try fewer or different words.</p></div>}
          <div className="grid-list" style={mode === "text" ? { gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" } : undefined}>
            {res.data.items.map((p) =>
              mode === "text" ? (
                <div key={p.id} className="text-hit">
                  <Thumb photo={p} onClick={() => nav(`/photo/${p.id}`)} caption={p.name} />
                  {p.snippet && <Snippet text={p.snippet} />}
                </div>
              ) : (
                <Thumb key={p.id} photo={p} onClick={() => nav(`/photo/${p.id}`)} caption={`${p.score?.toFixed(3)} · ${p.name}`} />
              ),
            )}
          </div>
        </>
      )}
    </div>
  );
}
