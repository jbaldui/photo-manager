"""Auto Process pipelines."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from .. import autoprocess, db
from ..autoprocess import PipelineConfig

router = APIRouter(prefix="/api")


class PipelineIn(BaseModel):
    name: str
    config: PipelineConfig


@router.get("/pipelines")
def pipelines():
    return autoprocess.list_pipelines()


@router.post("/pipelines")
def create(body: PipelineIn):
    with db.tx() as c:
        pid = c.execute("INSERT INTO pipelines(name, config_json, builtin) VALUES(?,?,0)",
                        (body.name.strip() or "Pipeline", body.config.model_dump_json())).lastrowid
    return autoprocess.get_pipeline(pid)


@router.put("/pipelines/{pid}")
def update(pid: int, body: PipelineIn):
    if not autoprocess.get_pipeline(pid):
        raise HTTPException(404)
    with db.tx() as c:
        c.execute("UPDATE pipelines SET name=?, config_json=? WHERE id=?", (body.name.strip() or "Pipeline", body.config.model_dump_json(), pid))
    return autoprocess.get_pipeline(pid)


@router.delete("/pipelines/{pid}")
def delete(pid: int):
    p = autoprocess.get_pipeline(pid)
    if not p:
        raise HTTPException(404)
    if p["builtin"]:
        raise HTTPException(400, "Built-in pipelines can be edited but not deleted")
    with db.tx() as c:
        c.execute("DELETE FROM pipelines WHERE id=?", (pid,))
    return {"ok": True}


class RunIn(BaseModel):
    dry_run: bool = False


@router.post("/pipelines/{pid}/run")
def run(pid: int, body: RunIn):
    try:
        return autoprocess.runner.start(pid, body.dry_run)
    except KeyError:
        raise HTTPException(404)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))


@router.get("/auto/status")
def status():
    return autoprocess.runner.status()


@router.post("/auto/cancel")
def cancel():
    autoprocess.runner.cancel()
    return autoprocess.runner.status()
