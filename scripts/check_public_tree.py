"""Fail a release check when tracked source includes private runtime artifacts."""
from pathlib import PurePosixPath
import re
import subprocess


PRIVATE_PARTS = {'.local', '.venv', '.models', 'node_modules', 'dist', '__pycache__', 'snapshot'}


def violations(paths):
    result = []
    for name in paths:
        path = PurePosixPath(name)
        filename = path.name.casefold()
        forbidden = (bool(PRIVATE_PARTS.intersection(path.parts))
                     or filename.startswith(('client_secret', '.env')) and filename != '.env.example'
                     or filename == 'google-client.json'
                     or bool(re.search(r'api[-_]key.*\.(txt|json)$', filename))
                     or bool(re.search(r'\.(sqlite3|db)(?:-.*)?$', filename))
                     or filename.endswith('.eml') and not name.startswith('tests/fixtures/'))
        if forbidden:
            result.append(name)
    return result


def main():
    tracked = subprocess.run(['git', 'ls-files', '-z'], check=True, capture_output=True).stdout.decode().split('\0')
    bad = violations(name for name in tracked if name)
    if bad:
        print('Private or experimental artifacts are tracked:')
        for path in bad:
            print(path)
        return 1
    print('Tracked source excludes private runtime artifacts and experimental service code.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
