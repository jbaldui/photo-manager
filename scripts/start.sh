#!/usr/bin/env bash
# Production-style: build the UI once, then a single process serves UI + API on http://127.0.0.1:8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -d "$ROOT/frontend/dist" ] || [ "${REBUILD:-0}" = "1" ]; then
  (cd "$ROOT/frontend" && npm run build)
fi
source "$ROOT/backend/.venv/bin/activate"
cd "$ROOT/backend"
echo "Photo Manager running at http://127.0.0.1:8000"
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
