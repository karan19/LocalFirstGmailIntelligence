"""Shared setup and doctor layer, deliberately independent of any future UI."""
import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / 'config.json').read_text())
LAYA_PATH = ROOT / '.models' / 'laya'
VENV_PYTHON = ROOT / '.venv' / 'bin' / 'python'
OLLAMA_URL = 'http://127.0.0.1:11434'


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    action: str = ''


def run(args, timeout=30, offline=False):
    env = os.environ.copy()
    if offline:
        env.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    return subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True,
                          timeout=timeout, check=True).stdout.strip()


def api(path, body=None, timeout=10):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(OLLAMA_URL + path, data=data,
                                     headers={'Content-Type': 'application/json'})
    # Never route local inference through proxy environment variables.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def platform_check():
    system, arch = platform.system(), platform.machine()
    version = platform.mac_ver()[0]
    major = int(version.split('.')[0]) if version else 0
    ok = system == 'Darwin' and arch == 'arm64' and major >= CONFIG['minimum_macos_major']
    return Check('Apple Silicon / platform', ok, f'{system} {version} / {arch}',
                 'Use native arm64 Python on Apple Silicon with macOS 14 or newer.')


def memory_check():
    try:
        gib = int(run(['/usr/sbin/sysctl', '-n', 'hw.memsize'])) / 1024**3
        minimum = float(os.environ.get('V0_MIN_MEMORY_GIB', CONFIG['minimum_memory_gib']))
        if not math.isfinite(minimum) or minimum <= 0:
            raise ValueError('memory threshold must be finite and positive')
        return Check('Hardware / memory', gib >= minimum,
                     f'{gib:.1f} GiB total; {minimum:g} GiB provisional minimum',
                     'Free memory or use a machine meeting the configured minimum. '
                     'V0_MIN_MEMORY_GIB overrides the provisional threshold; rerun ./doctor.sh.')
    except Exception as exc:
        return Check('Hardware / memory', False, str(exc), 'Check sysctl and V0_MIN_MEMORY_GIB.')


def weights_ready():
    manifest = json.loads((ROOT / 'readiness/laya-manifest.json').read_text())
    required = ['model.safetensors', 'rl_agent_config.json', 'encoder/config.json',
                'tokenizer/tokenizer.json', 'tokenizer/tokenizer_config.json', 'mlx_config.json']
    for name in required:
        path = LAYA_PATH / name
        expected = manifest['files'][name]
        if not path.is_file() or path.stat().st_size != expected['bytes']:
            return False
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected['sha256']:
                return False
    return True


def worker(operation, *args):
    return run([str(VENV_PYTHON), '-m', 'readiness.worker', operation, *map(str, args)],
               timeout=CONFIG['smoke_timeout_seconds'], offline=True)


def validate_laya(result):
    answer = result['answers']['signal']
    probs = answer['probabilities']
    if set(probs) != {'red', 'green', 'blue'}:
        raise ValueError('Laya returned unexpected choices')
    values = list(probs.values())
    if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError('Laya probabilities must be finite and in [0, 1]')
    if abs(sum(values) - 1) > 0.001:
        raise ValueError('Laya probabilities do not sum to one')
    if answer['choice'] != 'green' or probs['green'] != max(values):
        raise ValueError('Laya did not choose the explicit green signal')
    return (f"choice={answer['choice']}; probabilities={probs}"
            + (f"; peak MLX allocation={result['readiness_metrics']['mlx_peak_memory_bytes'] / 1024**2:.1f} MiB"
               if 'readiness_metrics' in result else ''))


def failure_detail(exc):
    if isinstance(exc, subprocess.CalledProcessError):
        return (exc.stderr or exc.stdout or str(exc))[-1600:].strip()
    return str(exc)


def doctor(json_output=False):
    checks = [platform_check()]
    if checks[0].passed:
        checks.append(memory_check())
    else:
        checks.append(Check('Hardware / memory', False, 'Unsupported platform', checks[0].action))
    supported = all(c.passed for c in checks)
    python_ok = (VENV_PYTHON.is_file() and Path(sys.prefix).resolve() == (ROOT / '.venv').resolve()
                 and sys.version_info >= (3, 11))
    checks.append(Check('Python environment', python_ok, f'{sys.executable} ({platform.python_version()})',
                        'Run ./setup.sh using native Python 3.11 or newer.'))

    def attempt(name, function, action, enabled=True):
        if not enabled:
            check = Check(name, False, 'Prerequisite failed; test not run', action)
        else:
            try:
                check = Check(name, True, function())
            except Exception as exc:
                check = Check(name, False, failure_detail(exc), action)
        checks.append(check)
        return check.passed

    mlx = attempt('MLX', lambda: worker('mlx'), 'Run ./setup.sh to install MLX; check Metal support.', supported and python_ok)
    def laya_import():
        result = json.loads(worker('laya-import'))
        if result['version'] != '0.2.0':
            raise ValueError('Expected laya-mlx==0.2.0; rerun ./setup.sh')
        return 'laya-mlx ' + result['version']

    laya = attempt('Laya / Laya-MLX', laya_import,
                   'Run ./setup.sh to install the pinned laya-mlx runtime.', mlx)
    weights = weights_ready()
    checks.append(Check('Laya weights', weights, str(LAYA_PATH),
                        'Run ./setup.sh and confirm the Laya model download.'))
    ollama_executable = shutil.which('ollama')
    checks.append(Check('Ollama executable', bool(ollama_executable), ollama_executable or 'Not installed',
                        'Install Ollama: brew install --cask ollama; then open -a Ollama.'))
    tags = {}

    def service():
        tags.update(api('/api/tags'))
        if not isinstance(tags.get('models'), list):
            raise ValueError('Unexpected Ollama model index')
        return OLLAMA_URL + ' is responding'

    service_ok = attempt('Ollama service', service, 'Start Ollama: open -a Ollama, or run ollama serve in another terminal.', supported)
    model = CONFIG['qwen_model']
    model_ok = any(m.get('name') == model or m.get('model') == model for m in tags.get('models', []))
    checks.append(Check('Qwen model', model_ok, model,
                        'Run ./setup.sh and confirm the Qwen download.'))

    def qwen_smoke():
        response = api('/api/chat', {'model': model, 'stream': False, 'think': False,
                                    'messages': [{'role': 'user', 'content': 'Reply with the single word READY.'}],
                                    'options': {'temperature': 0, 'seed': 0, 'num_ctx': 2048, 'num_predict': 24},
                                    'keep_alive': 0}, CONFIG['smoke_timeout_seconds'])
        text = response.get('message', {}).get('content', '').strip()
        if response.get('done') is not True or not text or response.get('eval_count', 0) <= 0:
            raise ValueError('Ollama did not complete local text generation')
        return f'Generated {response["eval_count"]} tokens: {text}'

    attempt('Qwen smoke test', qwen_smoke, 'Check Ollama logs and memory; rerun ./doctor.sh.', supported and service_ok and model_ok)
    attempt('Laya smoke test', lambda: validate_laya(json.loads(worker('laya-smoke', LAYA_PATH))),
            'Run ./setup.sh to repair dependencies/weights, then ./doctor.sh.', supported and laya and weights)
    ready = all(c.passed for c in checks)
    if json_output:
        print(json.dumps({'environment_ready': ready, 'checks': [asdict(c) for c in checks]}, indent=2))
    else:
        for c in checks:
            print(f'{c.name:26} {"PASS" if c.passed else "FAIL"}  {c.detail}')
            if not c.passed:
                print(f'  Next: {c.action} Verify: ./doctor.sh')
        print(f'\nEnvironment Ready: {"YES" if ready else "NO"}')
        if ready:
            print('Your machine is ready.')
    return 0 if ready else 1


def confirm_download(name, size, purpose, destination, approved):
    print(f'\nModel: {name}\nApproximate download: {size} GB\nPurpose: {purpose}\nDestination: {destination}', flush=True)
    if approved:
        print('Download approved with --download-models.')
        return True
    try:
        return input('Download this model? [y/N] ').strip().lower() in ('y', 'yes')
    except EOFError:
        return False


def setup(approved=False):
    for check in [platform_check(), memory_check()]:
        print(f'{check.name}: {"PASS" if check.passed else "FAIL"} — {check.detail}', flush=True)
        if not check.passed:
            print(check.action)
            return 1
    if sys.version_info < (3, 11):
        print('Python 3.11+ required. Install: brew install python@3.13; '
              'rerun V0_PYTHON=python3.13 ./setup.sh')
        return 1
    if not VENV_PYTHON.exists():
        print('Creating project-owned .venv', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    marker = ROOT / '.venv' / '.v0-dependencies'
    fingerprint = hashlib.sha256((ROOT / 'requirements.txt').read_bytes()).hexdigest()
    installed = False
    if marker.is_file() and marker.read_text() == fingerprint:
        try:
            run([str(VENV_PYTHON), '-m', 'pip', 'check'])
            installed = json.loads(worker('laya-import'))['version'] == '0.2.0'
            worker('mlx')
        except Exception:
            pass
    if installed:
        print('Project dependencies are healthy; skipping installation.', flush=True)
    else:
        print('Installing project dependencies inside .venv', flush=True)
        subprocess.run([str(VENV_PYTHON), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
        marker.write_text(fingerprint)
    if not shutil.which('ollama'):
        print('Ollama is missing. Install: brew install --cask ollama\n'
              'Then: open -a Ollama\nRerun: ./setup.sh')
        return verify()
    try:
        tags = api('/api/tags')
    except Exception:
        print('Ollama service is unavailable. Start: open -a Ollama\n'
              'Or run ollama serve in another terminal. Then rerun ./setup.sh')
        return verify()
    model = CONFIG['qwen_model']
    if any(m.get('name') == model or m.get('model') == model for m in tags['models']):
        print(f'{model} already installed; skipping download.')
    elif confirm_download(model, CONFIG['qwen_download_gb'], 'Local text-generation smoke test',
                          os.environ.get('OLLAMA_MODELS', str(Path.home() / '.ollama/models')) + ' (managed by Ollama service)', approved):
        subprocess.run(['ollama', 'pull', model], env={**os.environ, 'OLLAMA_HOST': OLLAMA_URL}, check=True)
    if weights_ready():
        print('Pinned Laya weights already present; skipping download.')
    elif confirm_download(CONFIG['laya_model'], CONFIG['laya_download_gb'],
                          'Local structured probabilistic decision smoke test', str(LAYA_PATH), approved):
        subprocess.run([str(VENV_PYTHON), '-m', 'readiness.worker', 'download'], cwd=ROOT, check=True)
    return verify()


def verify():
    return subprocess.call([str(VENV_PYTHON), '-m', 'readiness.cli', 'doctor'], cwd=ROOT)


def main():
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description='V0 local environment setup and readiness')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('doctor').add_argument('--json', action='store_true', help='Machine-readable readiness results')
    commands.add_parser('setup').add_argument('--download-models', action='store_true',
                                             help='Explicitly consent to the model downloads described in config.json/README')
    args = parser.parse_args()
    try:
        return doctor(args.json) if args.command == 'doctor' else setup(args.download_models)
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Environment Ready: NO\nOperation failed: {failure_detail(exc)}\n'
              'Next: fix the reported issue, rerun ./setup.sh, then ./doctor.sh.', file=sys.stderr)
        if args.command == 'setup' and VENV_PYTHON.exists() and not isinstance(exc, KeyboardInterrupt):
            verify()
        return 1


if __name__ == '__main__':
    sys.exit(main())
