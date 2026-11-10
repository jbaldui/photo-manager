import { useEffect, useRef } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { num } from "./format";
import Dashboard from "./pages/Dashboard";
import Library from "./pages/Library";
import Groups from "./pages/Groups";
import GroupDetail from "./pages/GroupDetail";
import PhotoPage from "./pages/PhotoPage";
import Search from "./pages/Search";
import Scan from "./pages/Scan";
import Settings from "./pages/Settings";
import Organize from "./pages/Organize";

export function useScanStatus() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["scan-status"],
    queryFn: api.scanStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : 5000),
  });
  // When a run finishes, refresh everything derived from the DB.
  const wasRunning = useRef(false);
  useEffect(() => {
    const running = !!q.data?.running;
    if (wasRunning.current && !running) qc.invalidateQueries({ predicate: (x) => x.queryKey[0] !== "scan-status" });
    wasRunning.current = running;
  }, [q.data?.running, qc]);
  return q;
}

export default function App() {
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: api.stats });
  const { data: scan } = useScanStatus();
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
          <NavLink to="/search">Search</NavLink>
          <NavLink to="/organize">Organize</NavLink>
          <NavLink to="/scan">Folders &amp; scan</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="spacer" />
        {scan?.running && cur && (
          <NavLink to="/scan" className="mini-progress" style={{ textDecoration: "none" }}>
            <div style={{ fontWeight: 600, color: "var(--text)" }}>{cur.label}</div>
            <div className="meter" style={{ margin: "6px 0" }}>
              <div style={{ width: cur.total ? `${(100 * cur.processed) / cur.total}%` : "30%" }} />
            </div>
            <div>{cur.message}</div>
          </NavLink>
        )}
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/groups/:id" element={<GroupDetail />} />
          <Route path="/photo/:id" element={<PhotoPage />} />
          <Route path="/search" element={<Search />} />
          <Route path="/scan" element={<Scan />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/organize" element={<Organize />} />
        </Routes>
      </main>
    </div>
  );
}
