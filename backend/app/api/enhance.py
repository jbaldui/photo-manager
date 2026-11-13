"""Enhance: merge copies of a photo into one improved image (results live in DATA_DIR/generated)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, enhance, sr_models
from ..enhance import EnhanceOptions

router = APIRouter(prefix="/api")


@router.get("/enhance/models")
def models():
    return [{"key": k, "label": m["label"], "downloaded": sr_models.is_downloaded(k)} for k, m in sr_models.MODELS.items()]


@router.post("/enhance/estimate")
def estimate(o: EnhanceOptions):
    try:
        return enhance.estimate(o)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/enhance")
def start(o: EnhanceOptions):
    try:
        return enhance.job.start(o)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/enhance/status")
def status():
    return enhance.job.status()


@router.post("/enhance/cancel")
def cancel():
    enhance.job.cancel.set()
    return enhance.job.status()


@router.get("/generated")
def generated(group_id: int | None = None):
    items = enhance.list_all()
    if group_id is not None:
        items = [i for i in items if i["group_id"] == group_id]
    return items


@router.get("/generated/{gid}")
def one(gid: int):
    d = enhance.get(gid)
    if not d:
        raise HTTPException(404)
    return d


@router.get("/generated/{gid}/image")
def image(gid: int, download: bool = False):
    d = enhance.get(gid)
    p = (config.GENERATED_DIR / d["path"]).resolve() if d and d["path"] else None
    if not p or config.GENERATED_DIR.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name if download else None, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/generated/{gid}")
def delete(gid: int):
    if not enhance.delete(gid):
        raise HTTPException(404)
    return {"ok": True}


class SaveIn(BaseModel):
    dest: str


@router.post("/generated/{gid}/save")
def save(gid: int, body: SaveIn):
    try:
        return {"path": enhance.save_copy(gid, body.dest)}
    except KeyError:
        raise HTTPException(404)
    except ValueError as e:
        raise HTTPException(400, str(e))
