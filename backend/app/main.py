"""FastAPI entrypoint. Run:  uvicorn app.main:app --port 8000  (from backend/)"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import autoprocess, config, db, maintenance
from .api import auto, enhance, groups, library, organize, removals, system

db.init()
_repaired = maintenance.repair()   # heal leftovers from older versions (e.g. stale duplicate groups)
if _repaired:
    print(f"Photo Manager: repaired library: {_repaired}", flush=True)
autoprocess.seed_presets()

app = FastAPI(title="Photo Manager", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(system.router)
app.include_router(library.router)
app.include_router(groups.router)
app.include_router(organize.router)
app.include_router(removals.router)
app.include_router(auto.router)
app.include_router(enhance.router)

# Serve the built React app (npm run build) so a single process is enough in "production".
if config.FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = config.FRONTEND_DIST / full_path
        if full_path and f.is_file() and config.FRONTEND_DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(config.FRONTEND_DIST / "index.html")
