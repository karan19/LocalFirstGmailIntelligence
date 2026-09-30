import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from readiness import cli


class ReadinessTests(unittest.TestCase):
    def test_rejects_invalid_decision_probabilities(self):
        for probs in ({'red': 0, 'green': float('nan'), 'blue': 0},
                      {'red': 0, 'green': 0.5, 'blue': 0},
                      {'red': 1.1, 'green': -0.1, 'blue': 0}):
            with self.assertRaises(ValueError):
                cli.validate_laya({'answers': {'signal': {'choice': 'green', 'probabilities': probs}}})

    def test_rejects_wrong_semantic_decision(self):
        with self.assertRaises(ValueError):
            cli.validate_laya({'answers': {'signal': {'choice': 'red',
                                'probabilities': {'red': 0.8, 'green': 0.1, 'blue': 0.1}}}})

    def test_complete_decision(self):
        self.assertIn('green', cli.validate_laya({'answers': {'signal': {'choice': 'green',
                      'probabilities': {'red': 0.1, 'green': 0.8, 'blue': 0.1}}}}))

    def test_unsupported_platform_never_installs(self):
        with patch.object(cli, 'platform_check', return_value=cli.Check('platform', False, 'unsupported', 'Use macOS')), \
             patch.object(cli, 'memory_check', return_value=cli.Check('memory', True, '64')), \
             patch.object(cli.subprocess, 'run') as process, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.setup(True), 1)
            process.assert_not_called()

    def test_unsupported_doctor_never_runs_models(self):
        output = io.StringIO()
        with patch.object(cli, 'platform_check', return_value=cli.Check('platform', False, 'unsupported')), \
             patch.object(cli, 'api') as api, patch.object(cli, 'worker') as worker, redirect_stdout(output):
            self.assertEqual(cli.doctor(True), 1)
            api.assert_not_called()
            worker.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['environment_ready'])

    def test_partial_download_is_not_ready(self):
        with tempfile.TemporaryDirectory() as path, patch.object(cli, 'LAYA_PATH', Path(path)):
            (Path(path) / 'model.safetensors').write_text('partial')
            self.assertFalse(cli.weights_ready())

    def test_offline_worker(self):
        with patch.object(cli.subprocess, 'run') as process:
            process.return_value.stdout = '{}'
            cli.worker('laya-smoke', '/local/model')
            self.assertEqual(process.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')
            self.assertEqual(process.call_args.kwargs['timeout'], cli.CONFIG['smoke_timeout_seconds'])

    def test_noninteractive_download_requires_consent(self):
        with patch('builtins.input', side_effect=EOFError), redirect_stdout(io.StringIO()):
            self.assertFalse(cli.confirm_download('model', 5, 'test', '/local', False))

    def test_setup_skips_installed_models_and_dependencies(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.venv/bin').mkdir(parents=True)
            (root / '.venv/bin/python').touch()
            (root / 'requirements.txt').write_text('laya-mlx==0.2.0')
            fingerprint = hashlib.sha256((root / 'requirements.txt').read_bytes()).hexdigest()
            (root / '.venv/.v0-dependencies').write_text(fingerprint)
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'VENV_PYTHON', root / '.venv/bin/python'), \
                 patch.object(cli, 'platform_check', return_value=cli.Check('platform', True, 'arm64')), \
                 patch.object(cli, 'memory_check', return_value=cli.Check('memory', True, '64 GiB')), \
                 patch.object(cli, 'run', return_value=''), \
                 patch.object(cli, 'worker', return_value='{"version":"0.2.0"}'), \
                 patch.object(cli, 'weights_ready', return_value=True), \
                 patch.object(cli.shutil, 'which', return_value='/usr/local/bin/ollama'), \
                 patch.object(cli, 'api', return_value={'models': [{'name': cli.CONFIG['qwen_model']}]}), \
                 patch.object(cli, 'confirm_download') as consent, \
                 patch.object(cli.subprocess, 'run') as process, \
                 patch.object(cli, 'verify', return_value=0) as verification, redirect_stdout(io.StringIO()):
                self.assertEqual(cli.setup(), 0)
                process.assert_not_called()
                consent.assert_not_called()
                verification.assert_called_once()


if __name__ == '__main__':
    unittest.main()
