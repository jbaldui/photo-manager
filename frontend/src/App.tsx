import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { num } from "./format";
import { useJobWatcher } from "./hooks";
import Dashboard from "./pages/Dashboard";
import Library from "./pages/Library";
import Groups from "./pages/Groups";
import GroupDetail from "./pages/GroupDetail";
import PhotoPage from "./pages/PhotoPage";
import Search from "./pages/Search";
import Scan from "./pages/Scan";
import Settings from "./pages/Settings";
import Organize from "./pages/Organize";
import AutoProcess from "./pages/AutoProcess";
import Quarantine from "./pages/Quarantine";
import Enhanced from "./pages/Enhanced";
import People from "./pages/People";

export const useScanStatus = () => useJobWatcher("scan-status", api.scanStatus);

function MiniProgress({ to, title, frac, message }: { to: string; title: string; frac: number | null; message?: string }) {
  return (
    <NavLink to={to} className="mini-progress" style={{ textDecoration: "none" }}>
      <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
      <div className="meter" style={{ margin: "6px 0" }}>
        <div style={{ width: frac != null ? `${100 * frac}%` : "30%" }} />
      </div>
      {message && <div>{message}</div>}
    </NavLink>
  );
}

export default function App() {
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const { data: scan } = useScanStatus();
  const { data: removal } = useJobWatcher("removal-status", api.removalStatus, true);
  const { data: auto } = useJobWatcher("auto-status", api.autoStatus, true);
  const { data: enh } = useJobWatcher("enhance-status", api.enhanceStatus);
  const dupCount = (stats?.tiers.exact?.groups ?? 0) + (stats?.tiers.duplicate?.groups ?? 0) + (stats?.tiers.near?.groups ?? 0);
  const cur = scan?.current ? scan.stages[scan.current] : null;

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <circle cx="9" cy="11" r="2" />
            <path d="m21 16-5-5-8 8" />
          </svg>
          Photo Manager
        </div>
        <nav className="nav" style={{ display: "contents" }}>
          <NavLink to="/" end>Overview</NavLink>
          <NavLink to="/library">
            Library <span className="count">{stats ? num(stats.total_files) : ""}</span>
          </NavLink>
          <NavLink to="/groups">
            Duplicates <span className="count">{stats ? num(dupCount) : ""}</span>
          </NavLink>
          <NavLink to="/people">People</NavLink>
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/organize">Organize</NavLink>
          <NavLink to="/auto">Auto Process</NavLink>
          <NavLink to="/quarantine">
            Quarantine <span className="count">{stats?.removed ? num(stats.removed) : ""}</span>
          </NavLink>
          <NavLink to="/enhanced">Enhanced</NavLink>
          <NavLink to="/scan">Folders &amp; scan</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="spacer" />
        {auto?.running && (
          <MiniProgress
            to="/auto"
            title={`Auto: ${auto.name}`}
            frac={auto.steps ? auto.steps.filter((x) => x.status === "done").length / Math.max(1, auto.steps.filter((x) => x.status !== "skipped").length) : null}
            message={auto.steps?.find((x) => x.status === "running")?.message}
          />
        )}
        {scan?.running && cur && !auto?.running && (
          <MiniProgress to="/scan" title={cur.label} frac={cur.total ? cur.processed / cur.total : null} message={cur.message} />
        )}
        {removal?.running && !auto?.running && (
          <MiniProgress
            to="/quarantine"
            title={removal.mode === "restore" ? "Restoring from quarantine" : "Moving to quarantine"}
            frac={removal.total ? (removal.done ?? 0) / removal.total : null}
            message={`${num(removal.done)} / ${num(removal.total)}`}
          />
        )}
        {enh?.running && <MiniProgress to="/enhanced" title="Enhancing" frac={enh.progress ?? null} message={enh.message} />}
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/groups/:id" element={<GroupDetail />} />
          <Route path="/photo/:id" element={<PhotoPage />} />
          <Route path="/people" element={<People />} />
          <Route path="/search" element={<Search />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/organize" element={<Organize />} />
          <Route path="/auto" element={<AutoProcess />} />
          <Route path="/quarantine" element={<Quarantine />} />
          <Route path="/enhanced" element={<Enhanced />} />
        </Routes>
      </main>
    </div>
  );
}
