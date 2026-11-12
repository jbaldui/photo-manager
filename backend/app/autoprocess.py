"""Auto Process: saved pipelines that refresh the library, decide, organize and quarantine in one go.

Order of steps (a run stops at the first failure):
  1. scan        refresh (find new / moved / deleted files and regroup) or the full analysis pipeline
  2. decide      keep the best copy, discard the rest, in undecided *exact* / *duplicate* groups only
  3. organize    write an organized COPY of every photo not marked discard (runs before anything is moved,
                 so e.g. a full disk stops the run while the library is still untouched)
  4. quarantine  move the photos this run discarded (optionally also earlier manual discards) to quarantine
A dry run executes nothing that touches files on disk and reports what each step would do.
"""
from __future__ import annotations

import json
import threading
import time
import traceback

from pydantic import BaseModel, Field, field_validator

from . import db, organize, quarantine
from .jobs import STAGES, runner as scan_runner

AUTO_TIERS = ("exact", "duplicate")


class ScanStep(BaseModel):
    mode: str = "refresh"                       # refresh (scan + group) | full (every analysis stage)

    @field_validator("mode")
    @classmethod
    def _mode(cls, v):
        if v not in ("refresh", "full"):
            raise ValueError("scan.mode must be 'refresh' or 'full'")
        return v


class DecideStep(BaseModel):
    enabled: bool = True
    tiers: list[str] = ["exact", "duplicate"]

    @field_validator("tiers")
    @classmethod
    def _tiers(cls, v):
        bad = [t for t in v if t not in AUTO_TIERS]
        if bad:
            raise ValueError(f"Automatic decisions are only allowed for exact and duplicate groups, not {bad}")
        return v


class OrganizeStep(BaseModel):
    enabled: bool = False
    options: dict = Field(default_factory=dict)  # organize.Options without scope (forced to not_discarded)


class QuarantineStep(BaseModel):
    enabled: bool = True
    dest: str | None = None
    include_manual_discards: bool = False


class PipelineConfig(BaseModel):
    scan: ScanStep = ScanStep()
    decide: DecideStep = DecideStep()
    organize: OrganizeStep = OrganizeStep()
    quarantine: QuarantineStep = QuarantineStep()

    def organize_options(self) -> organize.Options:
        o = dict(self.organize.options)
        o["scope"] = "not_discarded"
        o.setdefault("dest", "")
        return organize.Options(**o)


PRESETS = [
    ("Remove exact duplicates", PipelineConfig(decide=DecideStep(tiers=["exact"]))),
    (
        "Clean & organize",
        PipelineConfig(
            organize=OrganizeStep(enabled=True, options={"layout": "groups", "tiers": ["exact", "duplicate", "near"],
                                                         "rename": "duplicates", "pattern": "date_name"}),
        ),
    ),
    (
        "Organize only",
        PipelineConfig(
            decide=DecideStep(enabled=False),
            organize=OrganizeStep(enabled=True, options={"layout": "groups", "tiers": ["exact", "duplicate", "near"],
                                                         "rename": "duplicates", "pattern": "date_name"}),
            quarantine=QuarantineStep(enabled=False),
        ),
    ),
]


def seed_presets() -> None:
    conn = db.get()
    if conn.execute("SELECT 1 FROM pipelines WHERE builtin=1").fetchone():
        return
    with db.tx() as c:
        for name, cfg in PRESETS:
            c.execute("INSERT INTO pipelines(name, config_json, builtin) VALUES(?,?,1)", (name, cfg.model_dump_json()))


def get_pipeline(pid: int) -> dict | None:
    r = db.get().execute("SELECT * FROM pipelines WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["config"] = json.loads(d.pop("config_json"))
    d["last_result"] = json.loads(d["last_result"]) if d["last_result"] else None
    return d


def list_pipelines() -> list[dict]:
    return [get_pipeline(r[0]) for r in db.get().execute("SELECT id FROM pipelines ORDER BY builtin DESC, id")]


STEP_LABELS = {
    "scan": "Refresh library",
    "decide": "Decide (keep best copy)",
    "organize": "Organize copy",
    "quarantine": "Move duplicates to quarantine",
}


class AutoRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.state: dict = {"running": False}

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, default=str))

    def cancel(self) -> None:
        self._cancel.set()
        scan_runner.cancel()
        organize.job.cancel.set()
        quarantine.job.cancel.set()

    def start(self, pid: int, dry_run: bool = False) -> dict:
        from .locks import ensure_idle

        ensure_idle("auto")
        p = get_pipeline(pid)
        if not p:
            raise KeyError(pid)
        cfg = PipelineConfig(**p["config"])
        if cfg.organize.enabled:
            organize.validate_dest(cfg.organize_options().dest)     # fail fast with a clear message
        if not db.get().execute("SELECT 1 FROM roots").fetchone():
            raise ValueError("Add a folder first")
        steps = [("scan", True), ("decide", cfg.decide.enabled), ("organize", cfg.organize.enabled), ("quarantine", cfg.quarantine.enabled)]
        self._cancel.clear()
        self.state = {
            "running": True, "pipeline_id": pid, "name": p["name"], "dry_run": dry_run, "error": None,
            "started": time.time(), "finished": None, "current": None, "cancelled": False,
            "steps": [{"key": k, "label": STEP_LABELS[k], "status": "queued" if on else "skipped", "message": "", "result": None}
                      for k, on in steps],
        }
        self._thread = threading.Thread(target=self._run, args=(pid, cfg, dry_run), daemon=True, name="auto")
        self._thread.start()
        return self.status()

    # -------------------------------------------------------------- internals
    def _step(self, key: str, **kw) -> None:
        with self._lock:
            s = next(x for x in self.state["steps"] if x["key"] == key)
            s.update(kw)
            if kw.get("status") == "running":
                self.state["current"] = key

    def _run(self, pid: int, cfg: PipelineConfig, dry_run: bool) -> None:
        marked: list[int] = []
        try:
            for s in list(self.state["steps"]):
                if s["status"] == "skipped":
                    continue
                if self._cancel.is_set():
                    raise _Cancelled()
                key = s["key"]
                self._step(key, status="running")
                result = getattr(self, f"_do_{key}")(cfg, dry_run, marked)
                if self._cancel.is_set():
                    raise _Cancelled()
                self._step(key, status="done", result=result)
        except _Cancelled:
            with self._lock:
                self.state["cancelled"] = True
                for s in self.state["steps"]:
                    if s["status"] in ("running", "queued"):
                        s["status"] = "cancelled"
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            with self._lock:
                self.state["error"] = f"{e}" if isinstance(e, (ValueError, RuntimeError)) else f"{type(e).__name__}: {e}"
                for s in self.state["steps"]:
                    if s["status"] == "running":
                        s["status"] = "error"
                    elif s["status"] == "queued":
                        s["status"] = "not run"
        finally:
            with self._lock:
                self.state["running"] = False
                self.state["current"] = None
                self.state["finished"] = time.time()
            if not dry_run:
                with db.tx() as c:
                    c.execute("UPDATE pipelines SET last_run=?, last_result=? WHERE id=?",
                              (time.time(), json.dumps(self.status(), default=str), pid))

    def _do_scan(self, cfg: PipelineConfig, dry_run: bool, marked: list[int]) -> dict:
        stages = [k for k, _ in STAGES] if cfg.scan.mode == "full" else ["scan", "group"]
        if not scan_runner.start(stages):
            raise RuntimeError("A scan is already running")
        while scan_runner.running():
            st = scan_runner.status()
            cur = st.get("current")
            if cur:
                stg = st["stages"][cur]
                msg = stg["label"] + (f" — {stg['message']}" if stg.get("message") else "")
                self._step("scan", message=msg)
            time.sleep(0.3)
        st = scan_runner.status()
        if self._cancel.is_set():
            raise _Cancelled()
        if st.get("error"):
            raise RuntimeError(f"Scan failed: {st['error']}")
        r = st["stages"]["scan"]["result"] or {}
        self._step("scan", message=f"{r.get('seen', 0):,} photos · {r.get('new', 0):,} new · {r.get('missing', 0):,} gone")
        return r

    def _do_decide(self, cfg: PipelineConfig, dry_run: bool, marked: list[int]) -> dict:
        from .api.groups import decide_groups, preview_decide

        r = preview_decide(cfg.decide.tiers) if dry_run else decide_groups(cfg.decide.tiers)
        marked.extend(r["discarded"])
        self._step("decide", message=f"{r['groups_decided']:,} groups: keep {len(r['kept']):,}, discard {len(r['discarded']):,}")
        return {"groups_decided": r["groups_decided"], "kept": len(r["kept"]), "discarded": len(r["discarded"])}

    def _do_organize(self, cfg: PipelineConfig, dry_run: bool, marked: list[int]) -> dict:
        o = cfg.organize_options()
        if dry_run:
            plan = organize.build_plan(o)
            skip = set(marked)
            items = [i for i in plan["items"] if i["file_id"] not in skip]
            r = {"files": len(items), "bytes": sum(i["size"] for i in items), "dest": o.dest}
            self._step("organize", message=f"would copy {r['files']:,} photos")
            return r
        holder = {}

        def watch():
            while not holder.get("done"):
                st = organize.job.status()
                if st.get("total"):
                    self._step("organize", message=f"{st.get('done', 0):,} / {st['total']:,} copied")
                time.sleep(0.4)

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        try:
            st = organize.job.run_sync(o)
        finally:
            holder["done"] = True
        if st.get("cancelled"):
            raise _Cancelled()
        errors = len(st.get("errors") or [])
        self._step("organize", message=f"{st['done']:,} photos copied" + (f", {errors} errors" if errors else ""))
        if errors:
            raise RuntimeError(f"Organize: {errors} files could not be copied; nothing was moved to quarantine")
        return {"files": st["done"], "bytes": st["bytes_done"], "out": st["out"], "errors": errors}

    def _quarantine_options(self, cfg: PipelineConfig, marked: list[int], dry_run: bool) -> quarantine.RemovalOptions | None:
        name = self.state.get("name", "")
        if cfg.quarantine.include_manual_discards:
            if dry_run:
                manual = [r[0] for r in db.get().execute(
                    "SELECT f.id FROM files f JOIN decisions d ON d.root_id=f.root_id AND d.rel_path=f.rel_path"
                    " WHERE f.status='ok' AND d.decision='discard'")]
                return quarantine.RemovalOptions(dest=cfg.quarantine.dest, scope="files", file_ids=sorted(set(manual) | set(marked)), reason=f"auto:{name}")
            return quarantine.RemovalOptions(dest=cfg.quarantine.dest, scope="discarded", reason=f"auto:{name}")
        if not marked:
            return None
        return quarantine.RemovalOptions(dest=cfg.quarantine.dest, scope="files", file_ids=marked, reason=f"auto:{name}")

    def _do_quarantine(self, cfg: PipelineConfig, dry_run: bool, marked: list[int]) -> dict:
        o = self._quarantine_options(cfg, marked, dry_run)
        if o is None:
            self._step("quarantine", message="nothing to move")
            return {"files": 0, "bytes": 0}
        if dry_run:
            plan = quarantine.build_plan(o)
            self._step("quarantine", message=f"would move {plan['files']:,} photos ({len(plan['blocked'])} kept as last copy)")
            return {"files": plan["files"], "bytes": plan["bytes"], "blocked": len(plan["blocked"]), "dest": plan["dest"]}
        holder = {}

        def watch():
            while not holder.get("done"):
                st = quarantine.job.status()
                if st.get("total"):
                    self._step("quarantine", message=f"{st.get('done', 0):,} / {st['total']:,} moved")
                time.sleep(0.4)

        t = threading.Thread(target=watch, daemon=True)
        t.start()
        try:
            try:
                st = quarantine.job.run_sync(o)
            except ValueError as e:
                if "Nothing to move" in str(e):
                    self._step("quarantine", message="nothing to move")
                    return {"files": 0, "bytes": 0}
                raise
        finally:
            holder["done"] = True
        self._step("quarantine", message=f"{st['moved']:,} photos moved to quarantine")
        return {"files": st["moved"], "bytes": st["bytes_done"], "batch_id": st["batch_id"], "dest": st.get("dest"),
                "skipped": st.get("skipped", 0), "errors": len(st.get("errors") or [])}


class _Cancelled(Exception):
    pass


runner = AutoRunner()
