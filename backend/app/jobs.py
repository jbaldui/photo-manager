"""Background pipeline runner with per-stage progress, ETA and cancellation."""
from __future__ import annotations

import json
import threading
import time
import traceback

from . import candidates, db, embed, faces, grouping, ocr, scanner

STAGES = [
    ("scan", "Scanning folders"),
    ("analyze", "Metadata, SHA-256, perceptual hashes, thumbnails"),
    ("embed", "CLIP embeddings"),
    ("ocr", "OCR: text in images"),
    ("faces", "Faces: detect and group people"),
    ("pairs", "Candidate pairs + ORB verification"),
    ("group", "Grouping"),
]


class _Ctx:
    def __init__(self, runner: "Runner", stage: str):
        self.r, self.stage = runner, stage

    def cancelled(self) -> bool:
        return self.r._cancel.is_set()

    def message(self, msg: str) -> None:
        with self.r._lock:
            self.r.state["stages"][self.stage]["message"] = msg

    def update(self, processed: int, total: int | None, msg: str | None = None) -> None:
        with self.r._lock:
            s = self.r.state["stages"][self.stage]
            s["processed"] = processed
            if total is not None:
                s["total"] = total
            if msg:
                s["message"] = msg
            el = time.time() - (s["started"] or time.time())
            if s["total"] and processed and el > 2:
                s["rate"] = round(processed / el, 2)
                s["eta_seconds"] = round((s["total"] - processed) / (processed / el))


class Runner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = self._fresh([])

    @staticmethod
    def _fresh(selected: list[str]) -> dict:
        return {
            "running": False,
            "scan_id": None,
            "current": None,
            "error": None,
            "started": None,
            "finished": None,
            "stages": {
                k: {"label": label, "status": "queued" if k in selected else "skipped", "processed": 0, "total": None,
                    "message": "", "started": None, "finished": None, "rate": None, "eta_seconds": None, "result": None}
                for k, label in STAGES
            },
        }

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, stages: list[str]) -> bool:
        if self.running():
            return False
        self._cancel.clear()
        self.state = self._fresh(stages)
        self._thread = threading.Thread(target=self._run, args=(stages,), daemon=True, name="pipeline")
        self._thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, stages: list[str]) -> None:
        with self._lock:
            self.state["running"] = True
            self.state["started"] = time.time()
        with db.tx() as c:
            scan_id = c.execute("INSERT INTO scans(started, state) VALUES(?, 'running')", (time.time(),)).lastrowid
        self.state["scan_id"] = scan_id
        fns = {
            "scan": lambda ctx: scanner.scan_roots(scan_id, ctx),
            "analyze": scanner.analyze_pending,
            "embed": embed.embed_pending,
            "ocr": ocr.ocr_pending,
            "faces": faces.faces_pending,
            "pairs": candidates.build_pairs,
            "group": grouping.build_groups,
        }
        final = "done"
        try:
            for key, _ in STAGES:
                if key not in stages:
                    continue
                if self._cancel.is_set():
                    final = "cancelled"
                    break
                with self._lock:
                    self.state["current"] = key
                    s = self.state["stages"][key]
                    s["status"], s["started"] = "running", time.time()
                result = fns[key](_Ctx(self, key))
                with self._lock:
                    s["status"] = "cancelled" if self._cancel.is_set() else "done"
                    s["finished"], s["result"], s["eta_seconds"] = time.time(), result, 0
            if self._cancel.is_set():
                final = "cancelled"
        except Exception as e:  # noqa: BLE001
            final = "error"
            with self._lock:
                self.state["error"] = f"{type(e).__name__}: {e}"
                cur = self.state["current"]
                if cur:
                    self.state["stages"][cur]["status"] = "error"
            traceback.print_exc()
        finally:
            with self._lock:
                self.state["running"] = False
                self.state["current"] = None
                self.state["finished"] = time.time()
            with db.tx() as c:
                c.execute(
                    "UPDATE scans SET finished=?, state=?, summary=? WHERE id=?",
                    (time.time(), final, json.dumps({k: v["result"] for k, v in self.state["stages"].items()}), scan_id),
                )


runner = Runner()
