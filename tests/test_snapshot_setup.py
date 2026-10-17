"""Synthetic config and fake Keychain only. No actual main setup is accessed."""
import json
import stat
import tempfile
import unittest
from pathlib import Path

from snapshot.setup import import_existing_setup, MAIN_NAMESPACE, SNAPSHOT_NAMESPACE, MODIFY_SCOPE
from snapshot.launch import arguments


class FakeKeyring:
    def __init__(self, values):
        self.values, self.writes = dict(values), []

    def get_password(self, service, client_id):
        return self.values.get((service, client_id))

    def set_password(self, service, client_id, value):
        self.writes.append((service, client_id))
        self.values[service, client_id] = value


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'synthetic-main.json'
        self.source.write_text(json.dumps({'installed': {'client_id': 'fixture', 'client_secret': 'synthetic-secret'}}))
        self.destination = Path(self.temp.name) / 'private' / 'google-client.json'
        self.tokens = json.dumps({'refresh_token': 'synthetic-refresh', 'scope': MODIFY_SCOPE})

    def test_import_preserves_main_and_private_file_modes(self):
        keyring = FakeKeyring({(MAIN_NAMESPACE, 'fixture'): self.tokens})
        original = self.source.read_bytes()
        result = import_existing_setup(self.source, self.destination, keyring)
        self.assertEqual(result['authorization'], 'imported_main')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(keyring.values[MAIN_NAMESPACE, 'fixture'], self.tokens)
        self.assertEqual(json.loads(keyring.values[SNAPSHOT_NAMESPACE, 'fixture']), json.loads(self.tokens))
        self.assertEqual(keyring.writes, [(SNAPSHOT_NAMESPACE, 'fixture')])
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        self.assertNotIn('synthetic-refresh', json.dumps(result))

    def test_existing_snapshot_wins_and_source_is_not_read_again(self):
        keyring = FakeKeyring({(MAIN_NAMESPACE, 'fixture'): self.tokens})
        import_existing_setup(self.source, self.destination, keyring)
        keyring.writes.clear()
        self.source.unlink()
        result = import_existing_setup(self.source, self.destination, keyring)
        self.assertEqual(result['authorization'], 'existing_snapshot')
        self.assertFalse(result['configuration_imported'])
        self.assertEqual(keyring.writes, [])

    def test_configuration_only_never_reads_keychain(self):
        class UnavailableKeyring:
            def get_password(self, *_):
                raise AssertionError('Configuration-only import must not read Keychain')
        result = import_existing_setup(self.source, self.destination, UnavailableKeyring(), import_authorization=False)
        self.assertTrue(self.destination.exists())
        self.assertEqual(result['authorization'], 'not_imported')

    def test_readonly_missing_and_malformed_grants_require_consent(self):
        for value in (None, 'not-json', '[]', json.dumps({'scope': MODIFY_SCOPE}),
                      json.dumps({'scope': 'https://www.googleapis.com/auth/gmail.readonly', 'refresh_token': 'fixture'}),
                      json.dumps({'scope': MODIFY_SCOPE + ' https://mail.google.com/', 'refresh_token': 'fixture'})):
            with self.subTest(value=value):
                keyring = FakeKeyring({(MAIN_NAMESPACE, 'fixture'): value})
                result = import_existing_setup(self.source, self.destination, keyring)
                self.assertEqual(result['authorization'], 'consent_required')
                self.assertEqual(keyring.writes, [])

    def test_existing_configuration_is_not_overwritten(self):
        self.destination.parent.mkdir()
        data = {'installed': {'client_id': 'other-fixture', 'client_secret': 'other-secret'}}
        self.destination.write_text(json.dumps(data))
        keyring = FakeKeyring({(MAIN_NAMESPACE, 'fixture'): self.tokens})
        result = import_existing_setup(self.source, self.destination, keyring)
        self.assertEqual(json.loads(self.destination.read_text()), data)
        self.assertEqual(result['authorization'], 'consent_required')
        self.assertEqual(keyring.writes, [])

    def test_symlink_destination_is_refused(self):
        self.destination.parent.mkdir()
        self.destination.symlink_to(self.source)
        with self.assertRaises(ValueError):
            import_existing_setup(self.source, self.destination, FakeKeyring({}))

    def test_import_requires_explicit_private_service_launch(self):
        with self.assertRaises(SystemExit):
            arguments(['--reuse-main-setup'])
        _, args = arguments(['--private-service', '--reuse-main-setup'])
        self.assertTrue(args.reuse_main_setup)

    def test_regular_session_requires_explicit_service_launch(self):
        with self.assertRaises(SystemExit):
            arguments(['--regular-session'])
        _, args = arguments(['--private-service', '--regular-session'])
        self.assertTrue(args.regular_session)


if __name__ == '__main__':
    unittest.main()
