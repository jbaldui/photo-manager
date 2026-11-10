"""Organize (copy-only): preview the plan, run it in the background, follow progress."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import organize
from ..organize import Options

router = APIRouter(prefix="/api/organize")


@router.post("/preview")
def preview(o: Options, sample: int = 300):
    try:
        dest = organize.validate_dest(o.dest)
        plan = organize.build_plan(o)
    except ValueError as e:
        raise HTTPException(400, str(e))
    items = plan.pop("items")
    plan["free_bytes"] = shutil.disk_usage(dest).free
    plan["sample"] = [{k: it[k] for k in ("file_id", "src", "dst", "group", "is_best", "renamed")} for it in items[:sample]]
    return plan


@router.post("/start")
def start(o: Options):
    try:
        return organize.job.start(o)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/status")
def status():
    return organize.job.status()


@router.post("/cancel")
def cancel():
    organize.job.cancel.set()
    return organize.job.status()


@router.get("/manifest")
def manifest():
    out = organize.job.status().get("out")
    p = Path(out) / "manifest.csv" if out else None
    if not p or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename="manifest.csv")
