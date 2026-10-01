#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo 'Run ./setup.sh first to prepare the local runtime.'
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo 'Install Node.js 22+ (https://nodejs.org), then rerun ./start.sh.'
  exit 1
fi
.venv/bin/python -m pip install -r requirements-v1.txt
(cd frontend && npm ci && npm run build)
printf '\nOpen http://127.0.0.1:8765 in your browser. Press Ctrl-C to stop.\n\n'
exec .venv/bin/python -m uvicorn app.server:app --host 127.0.0.1 --port 8765 --no-access-log
