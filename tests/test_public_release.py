"""Release boundaries: no private artifacts; install-check never starts Gmail UI."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts.check_public_tree import violations

ROOT = Path(__file__).resolve().parents[1]


class PublicTreeTests(unittest.TestCase):
    def test_rejects_private_runtime_files_but_allows_synthetic_fixtures(self):
        paths = ['app/server.py', 'tests/fixtures/one-click.eml', '.local/client.json',
                 'copy/reviews.sqlite3-wal', 'google-client.json', '.env.production',
                 'client_secret_123.json', 'jev-api-key.txt', 'export/message.eml',
                 'snapshot/private_service.py']
        self.assertEqual(violations(paths), paths[2:])
        self.assertEqual(violations(['.env.example', 'docs/privacy.md']), [])


class LauncherTests(unittest.TestCase):
    def fixture(self, directory, node_exit=0):
        root = Path(directory)
        (root / '.venv/bin').mkdir(parents=True)
        (root / 'frontend').mkdir()
        (root / 'bin').mkdir()
        shutil.copyfile(ROOT / 'start.sh', root / 'start.sh')
        log = root / 'commands.log'
        for name, path, code in [('python', root / '.venv/bin/python', 0),
                                 ('node', root / 'bin/node', node_exit),
                                 ('npm', root / 'bin/npm', 0)]:
            path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "$COMMAND_LOG"\nexit {code}\n')
            path.chmod(0o755)
        env = {**os.environ, 'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH'], 'COMMAND_LOG': str(log)}
        return root, log, env

    def test_check_mode_installs_explorer_without_starting_server(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, env = self.fixture(directory)
            result = subprocess.run(['/bin/sh', 'start.sh', '--check'], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text()
            self.assertIn('pip install -r requirements-app.txt', calls)
            self.assertIn('pip check', calls)
            self.assertIn('npm ci', calls)
            self.assertIn('npm run build', calls)
            self.assertIn('scripts/check_install.py', calls)
            self.assertNotIn('uvicorn', calls)

    def test_failed_node_preflight_does_not_install_or_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root, log, env = self.fixture(directory, node_exit=1)
            result = subprocess.run(['/bin/sh', 'start.sh'], cwd=root, env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('python ', log.read_text())
            self.assertNotIn('npm ', log.read_text())
