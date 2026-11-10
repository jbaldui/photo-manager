import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { BarList, Columns, Tile } from "../components/ui";
import { bytes, DATE_SOURCE_LABEL, num, pct, TIER_INFO } from "../format";

export default function Dashboard() {
  const nav = useNavigate();
  const { data: s, isLoading } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  if (isLoading || !s) return <div className="page"><p className="sub">Loading…</p></div>;

  if (s.total_files === 0 && s.pending === 0) {
    return (
      <div className="page">
        <div className="card empty">
          <h2>No photos indexed yet</h2>
          <p>Choose your photos folder and run the first scan. Your files are only read, never changed.</p>
          <Link to="/scan"><button className="primary">Choose folder &amp; scan</button></Link>
        </div>
      </div>
    );
  }

  const go = (params: Record<string, string>) => nav(`/library?${new URLSearchParams(params)}`);
  const reclaim = (s.tiers.exact?.reclaimable_bytes ?? 0);
  const reclaimDup = (s.tiers.duplicate?.reclaimable_bytes ?? 0);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="sub">
            {num(s.total_files)} photos · {bytes(s.total_bytes)} · {num(s.unique_images)} unique images
          </p>
        </div>
        {s.pending > 0 && <span className="badge warn">{num(s.pending)} files not analysed yet</span>}
      </div>

      <div className="tiles">
        <Tile label="Photos" value={num(s.total_files)} hint={bytes(s.total_bytes)} to="/library" />
        <Tile
          label="Exact copies"
          value={num(s.tiers.exact?.groups ?? 0)}
          hint={`groups · ${bytes(reclaim)} reclaimable`}
          to="/groups?tier=exact"
        />
        <Tile
          label="Duplicates"
          value={num(s.tiers.duplicate?.groups ?? 0)}
          hint={`groups · ${bytes(reclaimDup)} incl. copies`}
          to="/groups?tier=duplicate"
        />
        <Tile label="Edited versions" value={num(s.tiers.near?.groups ?? 0)} hint="crops, captions, filters" to="/groups?tier=near" />
        <Tile label="Similar" value={num(s.tiers.similar?.groups ?? 0)} hint="same scene / burst" to="/groups?tier=similar" />
        <Tile label="Same text" value={num(s.tiers.text?.groups ?? 0)} hint="groups of images sharing words" to="/groups?tier=text" />
        <Tile label="Photos with text" value={num(s.with_text)} hint={`OCR read ${pct(s.ocr_done, s.images)} of images`} to="/library?has_text=true" />
        <Tile label="Unreadable" value={num(s.errors)} hint={s.missing ? `${num(s.missing)} missing on disk` : "files that failed to decode"} to="/scan#errors" />
      </div>

      <div className="grid-2">
        <div className="card">
          <h2>Photos per year (date taken)</h2>
          <Columns data={s.years} onPick={(k) => go({ year: k })} />
          <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>
            “?” = no capture date in metadata or filename. Click a column to browse that year.
          </p>
        </div>
        <div className="card">
          <h2>Metadata coverage</h2>
          <div className="stack" style={{ gap: 12 }}>
            {[
              ["Has EXIF metadata", s.with_exif],
              ["Has a capture date", s.with_date],
              ["Has GPS location", s.with_gps],
              ["Analysed by CLIP", s.embedded, s.images],
              ["Read by OCR", s.ocr_done, s.images],
            ].map(([label, n, of]) => (
              <div key={label as string}>
                <div className="row" style={{ justifyContent: "space-between", fontSize: 13 }}>
                  <span>{label}</span>
                  <span className="mono">
                    {num(n as number)} · {pct(n as number, (of as number) ?? s.total_files)}
                  </span>
                </div>
                <div className="meter">
                  <div style={{ width: pct(n as number, (of as number) ?? s.total_files) }} />
                </div>
              </div>
            ))}
            <h3 style={{ marginTop: 6 }}>Where the date comes from</h3>
            <BarList data={s.date_sources} label={(k) => DATE_SOURCE_LABEL[k] ?? k} />
          </div>
        </div>
        <div className="card">
          <h2>Formats</h2>
          <BarList
            data={s.formats}
            label={(k) => k.toUpperCase()}
            value={(d) => `${num(d.count)} · ${bytes(d.bytes)}`}
            onPick={(k) => go({ ext: k })}
          />
        </div>
        <div className="card">
          <h2>Likely source</h2>
          <BarList data={s.sources} onPick={(k) => go({ source: k })} />
          <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>Guessed from filename patterns and EXIF (camera, software).</p>
        </div>
        <div className="card">
          <h2>Top cameras</h2>
          <BarList data={s.cameras} onPick={(k) => go({ camera: s.cameras.find((c) => c.key === k)?.model ?? k })} />
        </div>
        <div className="card">
          <h2>Similarity groups</h2>
          <table className="simple">
            <thead>
              <tr><th>Tier</th><th>Groups</th><th>Files</th><th>Reclaimable</th></tr>
            </thead>
            <tbody>
              {(["exact", "duplicate", "near", "similar", "text"] as const).map((t) => (
                <tr key={t}>
                  <td><Link to={`/groups?tier=${t}`}>{TIER_INFO[t].label}</Link></td>
                  <td className="mono">{num(s.tiers[t]?.groups ?? 0)}</td>
                  <td className="mono">{num(s.tiers[t]?.files ?? 0)}</td>
                  <td className="mono">{t === "similar" || t === "text" ? "–" : bytes(s.tiers[t]?.reclaimable_bytes ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="sub" style={{ fontSize: 12, marginTop: 8 }}>
            Tiers are nested views: a looser tier only lists groups that add images beyond the stricter one.
          </p>
        </div>
      </div>
    </div>
  );
}
