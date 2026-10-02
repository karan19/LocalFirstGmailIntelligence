#!/bin/sh
set -eu
PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_PATH="$PROJECT_ROOT/.local/winnow-runtime"
if [ ! -f "$RUNTIME_PATH/scripts/serve.py" ]; then
  echo 'Winnow runtime is missing. See README dual-classifier setup.' >&2
  exit 1
fi
exec python3 "$RUNTIME_PATH/scripts/serve.py" --profile apple-silicon --text-only --context 8192
