"""Validate native imports and app startup without Gmail, inference or downloads."""
import importlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    try:
        port = int(os.environ.get('LOCAL_APP_PORT', '8765'))
        if not 1024 <= port <= 65535:
            raise ValueError('LOCAL_APP_PORT must be between 1024 and 65535')
        for name in ('laya_mlx', 'fastembed', 'hnswlib', 'networkx', 'numpy', 'dkim', 'app.server'):
            importlib.import_module(name)
    except Exception as error:
        # Import errors are local installation diagnostics; no credentials are read.
        print(f'Application install check failed ({type(error).__name__}). '
              'Check LOCAL_APP_PORT, rerun ./setup.sh and ./start.sh --check.', file=sys.stderr)
        return 1
    print('Application and Explorer native imports passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
