"""Trusted-terminal launcher. Optional setup import runs only in private service."""
import argparse
import os
import secrets
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRECTORY = Path.home() / 'CleanUpFolderService'


def arguments(argv=None):
    parser = argparse.ArgumentParser(description='Experimental Updates snapshot launcher')
    parser.add_argument('--private-service', action='store_true', help='Enable only after a dummy-fixture isolation check')
    parser.add_argument('--regular-session', action='store_true', help='Explicitly run without protected-agent mailbox isolation')
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--client-config', type=Path, help='Defaults to google-client.json inside the private directory')
    parser.add_argument('--reuse-main-setup', action='store_true', help='Privately import the main client config and compatible Keychain grant without changing main')
    parser.add_argument('--reuse-main-client-config', action='store_true', help='Copy only the main Google client configuration; sign in independently')
    args = parser.parse_args(argv)
    if args.regular_session and not args.private_service:
        parser.error('--regular-session requires --private-service.')
    if args.client_config is None:
        args.client_config = args.directory / 'google-client.json'
    if args.reuse_main_setup and args.reuse_main_client_config:
        parser.error('Choose either full setup reuse or client configuration only.')
    if (args.reuse_main_setup or args.reuse_main_client_config) and not args.private_service:
        parser.error('--reuse-main-setup requires --private-service and a trusted-terminal launch.')
    return parser, args


def main():
    parser, args = arguments()
    env = dict(os.environ)
    children = []
    if args.private_service:
        if env.get('GMAIL_SNAPSHOT_ISOLATION_VERIFIED') != '1' and not args.regular_session:
            parser.error('Complete docs/snapshot.md isolation verification first; no private service was started.')
        from .private_service import private_path
        directory = private_path(str(args.directory))
        private_path(str(args.client_config))
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        env.update(GMAIL_SNAPSHOT_CAPABILITY=secrets.token_urlsafe(32), GMAIL_SNAPSHOT_DIRECTORY=str(directory),
                   GMAIL_SNAPSHOT_CLIENT_CONFIG=str(args.client_config.absolute()))
        private_env = dict(env)
        private_env['GMAIL_SNAPSHOT_REGULAR_SESSION'] = '1' if args.regular_session else '0'
        private_env['GMAIL_SNAPSHOT_REUSE_MAIN_SETUP'] = '1' if args.reuse_main_setup or args.reuse_main_client_config else '0'
        private_env['GMAIL_SNAPSHOT_IMPORT_AUTHORIZATION'] = '1' if args.reuse_main_setup else '0'
        if args.reuse_main_setup or args.reuse_main_client_config:
            private_env['GMAIL_SNAPSHOT_MAIN_CLIENT_CONFIG'] = os.environ.get('GMAIL_CLIENT_CONFIG', str(ROOT / '.local/google-client.json'))
        children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'snapshot.private_service:create_app', '--factory', '--host', '127.0.0.1', '--port', '9000', '--no-access-log', '--log-level', 'critical'], cwd=ROOT, env=private_env))
    else:
        env.pop('GMAIL_SNAPSHOT_CAPABILITY', None)
    children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'snapshot.ui_server:create_app', '--factory', '--host', '127.0.0.1', '--port', '8766', '--no-access-log'], cwd=ROOT, env=env))
    print('Snapshot UI: http://127.0.0.1:8766', flush=True)
    if args.private_service:
        print('Private service enabled. OAuth configuration is read only by that service; use Connect Gmail in the UI.', flush=True)
        if args.regular_session:
            print('Regular session mode: mailbox storage is not isolated from Codex filesystem access.', flush=True)
    else:
        print('Preview only. Gmail connection is disabled until the private service is launched from a trusted terminal.', flush=True)

    def stopped(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stopped)
    try:
        while all(child.poll() is None for child in children):
            try:
                children[-1].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
