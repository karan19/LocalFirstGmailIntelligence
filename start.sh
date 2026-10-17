#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--check' ]; }; then
  echo 'Usage: ./start.sh [--check]' >&2
  exit 2
fi
if [ ! -x .venv/bin/python ]; then
  echo 'Run ./setup.sh first to prepare the local runtime.' >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo 'Install Node.js 22+ (https://nodejs.org), then rerun ./start.sh.' >&2
  exit 1
fi
node -e 'if(Number(process.versions.node.split(".")[0])<22){console.error("Node.js 22+ required.");process.exit(1)}'
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python -m pip check
(cd frontend && npm ci && npm run build)
.venv/bin/python scripts/check_install.py
if [ "${1:-}" = '--check' ]; then
  echo 'Application install and build checks passed. No Gmail access or model inference was performed.'
  exit 0
fi
APP_PORT="${LOCAL_APP_PORT:-8765}"
printf '\nOpen http://127.0.0.1:%s in your browser. Press Ctrl-C to stop.\n\n' "$APP_PORT"
exec .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port "$APP_PORT" --no-access-log
