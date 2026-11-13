#!/usr/bin/env bash
# Production-style: build the UI when needed, then a single process serves UI + API on http://127.0.0.1:8000
# The UI is rebuilt automatically whenever the frontend sources are newer than the last build
# (set REBUILD=1 to force it). The backend always starts fresh, so new code and DB tables load.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FE="$ROOT/frontend"

needs_build() {
  [ "${REBUILD:-0}" = "1" ] && return 0
  [ -f "$FE/dist/index.html" ] || return 0
  [ -n "$(find "$FE/src" "$FE/index.html" "$FE/package.json" "$FE/vite.config.ts" -newer "$FE/dist/index.html" -print -quit 2>/dev/null)" ]
}

if needs_build; then
  echo "Building the UI..."
  [ -d "$FE/node_modules" ] || (cd "$FE" && npm install)
  (cd "$FE" && npm run build)
fi
source "$ROOT/backend/.venv/bin/activate"
cd "$ROOT/backend"
echo "Photo Manager running at http://127.0.0.1:8000"
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
