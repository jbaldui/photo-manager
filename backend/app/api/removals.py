"""Quarantine: move discarded photos out of the library (undoable)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import quarantine
from ..quarantine import RemovalOptions

router = APIRouter(prefix="/api/removals")


@router.get("/defaults")
def defaults():
    return {"dest": quarantine.default_dest()}


@router.post("/preview")
def preview(o: RemovalOptions, sample: int = 300):
    try:
        plan = quarantine.build_plan(o)
    except ValueError as e:
        raise HTTPException(400, str(e))
    items = plan.pop("items")
    plan["sample"] = [{k: it[k] for k in ("file_id", "src", "qrel", "size")} for it in items[:sample]]
    return plan


@router.post("/start")
def start(o: RemovalOptions):
    try:
        return quarantine.job.start(o)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/status")
def status():
    return quarantine.job.status()


@router.post("/cancel")
def cancel():
    quarantine.job.cancel.set()
    return quarantine.job.status()


@router.get("")
def list_batches(limit: int = 100):
    return quarantine.batches(limit)


@router.get("/{batch_id}")
def batch(batch_id: int):
    b = next((x for x in quarantine.batches(100000) if x["id"] == batch_id), None)
    if not b:
        raise HTTPException(404)
    return b | {"items": quarantine.batch_files(batch_id)}


@router.post("/{batch_id}/restore")
def restore(batch_id: int):
    try:
        return quarantine.job.start_restore(batch_id)
    except KeyError:
        raise HTTPException(404, "Batch not found")
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
