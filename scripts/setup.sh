#!/usr/bin/env bash
# One-time setup: creates the isolated Python venv (backend/.venv) and installs frontend deps.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/share/uv-bootstrap/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "uv not found - bootstrapping into ~/.local/share/uv-bootstrap"
  python3 -m venv "$HOME/.local/share/uv-bootstrap"
  "$HOME/.local/share/uv-bootstrap/bin/pip" install -q uv
fi
cd "$ROOT/backend"
"$UV" python install 3.12
"$UV" venv --python 3.12 .venv
"$UV" sync
if command -v npm >/dev/null; then
  (cd "$ROOT/frontend" && npm install)
else
  echo "npm not found - install Node 22 LTS, then run: cd frontend && npm install"
fi
echo "Setup complete."
