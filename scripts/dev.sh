#!/usr/bin/env bash
# Development: backend on :8000 (auto-reload) + Vite dev server on :5173 (proxies /api).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/backend/.venv/bin/activate"
(cd "$ROOT/backend" && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir app) &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd "$ROOT/frontend" && npm run dev
