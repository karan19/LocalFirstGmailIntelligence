#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${V0_PYTHON:-python3}"
fi
cd "$ROOT"
exec "$PYTHON" -m readiness.cli doctor "$@"
