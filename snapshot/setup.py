"""Explicit, service-only import of an existing local Google setup.

Importing this module performs no I/O. Never called by the public UI server.
"""
import json
import os
from pathlib import Path
import tempfile

MAIN_NAMESPACE = 'LocalFirstGmailIntelligence'
SNAPSHOT_NAMESPACE = 'LocalFirstGmailSnapshot'
MODIFY_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'


def client(path):
    value = json.loads(Path(path).read_text())
    installed = value['installed']
    if any(not isinstance(installed.get(k), str) or not installed[k] for k in ('client_id', 'client_secret')):
        raise ValueError('Invalid Desktop OAuth client configuration.')
    return value, installed['client_id']


def import_existing_setup(source, destination, keyring, *, import_authorization=True):
    """Copy once without replacing config or modifying the main Keychain entry.

    Returned values contain status only. Existing snapshot setup takes precedence.
    The caller validates that the destination is outside workspace storage.
    """
    source, destination = Path(source), Path(destination)
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('Unsafe private configuration path.')
    copied = False
    if not destination.exists():
        if any(p.is_symlink() for p in (source, *source.parents)):
            raise ValueError('Unsafe source configuration path.')
        value, _ = client(source)
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        destination.parent.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix='.client-import-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(value, stream)
                stream.flush()
                os.fsync(stream.fileno())
            # Publish without replacing another process's or user's config.
            try:
                os.link(temporary, destination)
                copied = True
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    _, client_id = client(destination)
    if not import_authorization:
        return {'configuration_imported': copied, 'authorization': 'not_imported'}
    if keyring.get_password(SNAPSHOT_NAMESPACE, client_id):
        return {'configuration_imported': copied, 'authorization': 'existing_snapshot'}
    raw = keyring.get_password(MAIN_NAMESPACE, client_id)
    if not raw:
        return {'configuration_imported': copied, 'authorization': 'consent_required'}
    try:
        tokens = json.loads(raw)
        compatible = (isinstance(tokens, dict)
                      and isinstance(tokens.get('refresh_token'), str) and bool(tokens['refresh_token'])
                      and isinstance(tokens.get('scope'), str)
                      and set(tokens['scope'].split()) == {MODIFY_SCOPE})
    except (ValueError, TypeError):
        compatible = False
    if not compatible:
        return {'configuration_imported': copied, 'authorization': 'consent_required'}
    keyring.set_password(SNAPSHOT_NAMESPACE, client_id, json.dumps(tokens))
    return {'configuration_imported': copied, 'authorization': 'imported_main'}
