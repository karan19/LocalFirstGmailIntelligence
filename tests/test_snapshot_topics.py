"""Synthetic-only topic discovery and browser boundaries."""
import asyncio
import base64
from email.message import EmailMessage
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from snapshot.topics import fit, prepare, select_records, encode_batches, MODEL_DIRECTORY
from snapshot.store import SnapshotStore
from snapshot.private_service import create_app, TopicOptions
from snapshot.ui_server import create_app as create_ui
from tests.test_snapshot_setup import FakeKeyring, SNAPSHOT_NAMESPACE, MODIFY_SCOPE


def original(body, subject='Synthetic update'):
    mail = EmailMessage()
    mail['Subject'] = subject
    mail.set_content(body)
    return {'raw': base64.urlsafe_b64encode(mail.as_bytes()).decode(), 'internalDate': '1000'}


class TopicTests(unittest.TestCase):
    def test_preparation_preserves_original_identity_and_removes_quotes(self):
        value = original('Hi Alice Smith, your receipt is ready.\n> Old quoted message\nOn Tuesday, Bob wrote:\nOld reply', 'Re: Your receipt')
        before = dict(value)
        subject, text = prepare(value)
        self.assertEqual(subject, 'Your receipt')
        self.assertIn('Alice Smith', text)
        self.assertNotIn('Old', text)
        self.assertEqual(before, value)

    def test_topic_options_reject_invalid_and_unexpected_input(self):
        for value in (True, 0, 4, 100001, '100'):
            with self.assertRaises(ValueError):
                TopicOptions(limit=value)
        with self.assertRaises(ValueError):
            TopicOptions(path='/elsewhere')
        self.assertEqual(TopicOptions().limit, 100)
        self.assertEqual(TopicOptions(limit=8700).limit, 8700)
        self.assertFalse(TopicOptions().ignore_attachments)
        with self.assertRaises(ValueError):
            TopicOptions(ignore_attachments='true')

    def test_automated_preheader_padding_does_not_consume_body_budget(self):
        padding = ('\u00a0\u200c' * 10000) + (' ' * 15000) + '&amp;#x20;'
        value = original('Lyft Thanks for riding!' + padding + '\nTrip receipt and fare details.', 'Your ride on October 7')
        _, text = prepare(value)
        self.assertIn('Trip receipt and fare details.', text)
        self.assertNotIn('\u200c', text)
        self.assertNotIn('&#', text)
        self.assertLess(len(text), 150)

    def test_attachment_filter_excludes_whole_email_and_preserves_original(self):
        attached = EmailMessage()
        attached['Subject'] = 'Synthetic receipt'
        attached.set_content('Your receipt is ready to review.')
        attached.add_attachment(b'synthetic file', maintype='application', subtype='pdf', filename='receipt.pdf')
        records = [{'id': 'attached', 'original': {'raw': base64.urlsafe_b64encode(attached.as_bytes()).decode()}},
                   {'id': 'plain', 'original': original('Your parcel has been delivered.') }]
        before = json.dumps(records)
        included, excluded = select_records(records)
        self.assertEqual([row['id'] for row in included], ['attached', 'plain'])
        self.assertEqual(excluded, 0)
        included, excluded = select_records(records, ignore_attachments=True)
        self.assertEqual([row['id'] for row in included], ['plain'])
        self.assertEqual(excluded, 1)
        self.assertEqual(json.dumps(records), before)

    def test_actual_100_message_pipeline_is_offline(self):
        records = []
        templates = [
            ('Shipment progress', 'Your parcel has shipped. The courier will deliver your package soon. Track delivery progress.'),
            ('Payment receipt', 'Your invoice payment has been received. The transaction total includes sales tax. Save this receipt.'),
            ('Account security', 'A new login was detected. Review this sign-in device and change your password if unrecognized.'),
        ]
        for index in range(100):
            if index < 7:
                subject = f'Your ride with Driver {index} on October {index + 1}'
                body = f'Lyft Thanks for riding with Driver {index}!' + '\u00a0\u200c' * 1000 + '\nTrip receipt. Your ride fare and distance. Lyft ride summary.'
            else:
                subject, body = templates[index % 3]
            records.append({'id': str(index), 'original': original(body + f' Reference {index}.', subject)})
        before = json.dumps(records)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('No network during topic analysis')):
            result = fit(records)
        self.assertEqual(result['processed'], 100)
        self.assertEqual(sum(group['count'] for group in result['groups']), 100)
        self.assertEqual({mid for group in result['groups'] for mid in group['members']}, {str(i) for i in range(100)})
        self.assertTrue(all(len(group['examples']) <= 3 for group in result['groups']))
        self.assertEqual(json.dumps(records), before)
        ride_groups = {group['topic'] for group in result['groups'] if set(group['members']) & {str(i) for i in range(7)}}
        self.assertEqual(len(ride_groups), 1)
        self.assertNotIn(-1, ride_groups)
        ride_group = next(group for group in result['groups'] if group['topic'] in ride_groups)
        self.assertEqual(set(ride_group['members']), {str(i) for i in range(7)})

    def test_8700_embeddings_use_bounded_batches_and_counts_only_progress(self):
        import numpy as np
        sizes, updates = [], []
        class Encoder:
            def encode(self, texts, **kwargs):
                sizes.append(len(texts))
                return np.array([[int(text)] * 384 for text in texts], dtype=np.float32)
        vectors = encode_batches(Encoder(), [str(i) for i in range(8700)],
                                 lambda *values: updates.append(values))
        self.assertEqual(vectors.shape, (8700, 384))
        self.assertEqual(vectors.nbytes, 8700 * 384 * 4)
        self.assertEqual(max(sizes), 32)
        self.assertEqual(sum(sizes), 8700)
        self.assertTrue(np.all(vectors[:, 0] == np.arange(8700)))
        self.assertEqual(updates[-1], ('embedding', 8700, 8700))

    def test_service_persists_account_scoped_groups_and_filters_mail_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'client.json'
            cfg.write_text(json.dumps({'installed': {'client_id': 'fixture', 'client_secret': 'fake-secret'}}))
            store = SnapshotStore(directory)
            job = store.create_job('fixture-account', 100)
            store.page(job, ['fixture-id'], None)
            store.outcome(job, 'fixture-id', 'saved', original('Alice Smith, your parcel is delivered.', 'Delivery update'))
            store.state(job, 'done')
            rid = store.create_topic_run('fixture-account', job)
            result = {'processed': 1, 'skipped': 0, 'seconds': 1, 'groups': [
                {'topic': 0, 'count': 1, 'keywords': ['parcel', 'delivery'], 'members': ['fixture-id'], 'examples': ['fixture-id']}]}
            store.finish_topic_run(rid, 'done', result)
            # A later run for another account must never replace the selected account's result.
            other = store.create_topic_run('other-account', job)
            store.finish_topic_run(other, 'done', {'groups': []})
            env = {'GMAIL_SNAPSHOT_ISOLATION_VERIFIED': '1', 'GMAIL_SNAPSHOT_REGULAR_SESSION': '1',
                   'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability', 'GMAIL_SNAPSHOT_DIRECTORY': directory,
                   'GMAIL_SNAPSHOT_CLIENT_CONFIG': str(cfg), 'GMAIL_SNAPSHOT_REUSE_MAIN_SETUP': '0'}
            keyring = FakeKeyring({(SNAPSHOT_NAMESPACE, 'fixture'): json.dumps({'scope': MODIFY_SCOPE, 'refresh_token': 'fake-refresh'})})
            real_client = httpx.AsyncClient
            writes = []
            trash_writes = []
            provider_account = ['fixture-account']
            def provider(request):
                if request.method == 'POST' and request.url.path.endswith('/trash'):
                    mid = request.url.path.split('/')[-2]
                    trash_writes.append(mid)
                    return httpx.Response(200, json={'id': mid, 'labelIds': ['TRASH']})
                if request.url.path.endswith('/labels') and request.method == 'GET':
                    return httpx.Response(200, json={'labels': [{'id': 'later-fixture', 'name': 'Later', 'type': 'user'}]})
                if '/messages/' in request.url.path and request.method == 'GET':
                    return httpx.Response(200, json={'id': request.url.path.split('/')[-1], 'labelIds': ['CATEGORY_UPDATES', 'INBOX']})
                if request.url.path.endswith('/messages/batchModify'):
                    payload = json.loads(request.content)
                    if payload['addLabelIds'] == ['TRASH']:
                        trash_writes.extend(payload['ids'])
                    else:
                        writes.append(payload)
                    return httpx.Response(204)
                if request.url.path.endswith('/token'):
                    return httpx.Response(200, json={'access_token': 'fake-access', 'scope': MODIFY_SCOPE})
                return httpx.Response(200, json={'emailAddress': provider_account[0]})
            def fake_client(*args, **kwargs):
                return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
            with patch.dict('os.environ', env), patch('snapshot.private_service.Keyring', return_value=keyring), patch('snapshot.private_service.httpx.AsyncClient', side_effect=fake_client):
                with TestClient(create_app(), base_url='http://127.0.0.1:9000') as client:
                    headers = {'x-snapshot-capability': 'synthetic-capability'}
                    self.assertEqual(client.get('/api/topics').status_code, 403)
                    client.post('/api/refresh', headers=headers)
                    status = client.get('/api/status', headers=headers).json()
                    self.assertEqual(status['topics']['status'], 'done')
                    self.assertNotIn('Alice', json.dumps(status))
                    groups = client.get('/api/topics', headers=headers).json()['groups']
                    self.assertEqual(groups[0]['keywords'], ['parcel', 'delivery'])
                    self.assertIn('Alice Smith', groups[0]['examples'][0]['text'])
                    self.assertNotIn('fixture-id', json.dumps(groups))
                    self.assertNotIn('fixture-account', json.dumps(groups))
                    with patch.dict('os.environ', {'GMAIL_SNAPSHOT_REGULAR_SESSION': '0'}):
                        self.assertEqual(client.get('/api/topics', headers=headers).status_code, 403)
                    # Exercise the start/worker path without a second full model fit.
                    for index in range(4):
                        mid = 'additional-' + str(index)
                        store.page(job, [mid], None)
                        store.outcome(job, mid, 'saved', original('Synthetic shipping notification'))
                    result['processed'] = 5
                    result['groups'][0]['count'] = 5
                    result['groups'][0]['members'] += ['additional-' + str(index) for index in range(4)]
                    async def fake_process(*args, **kwargs):
                        self.assertEqual(args[1:3], ('-m', 'snapshot.topics'))
                        self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'], '1')
                        self.assertIn('--ignore-attachments', args)
                        run_id = args[args.index('--run-id') + 1]
                        output = Path(directory) / 'topic-runs'
                        output.mkdir(mode=0o700, exist_ok=True)
                        (output / (run_id + '.json')).write_text(json.dumps(result))
                        class Process:
                            returncode = 0
                            stdout = asyncio.StreamReader()
                            async def wait(self):
                                await asyncio.sleep(.1)
                        process = Process()
                        process.stdout.feed_data(b'{"phase":"embedding","encoded":5,"total":5}\n')
                        process.stdout.feed_eof()
                        return process
                    with patch('snapshot.private_service.asyncio.create_subprocess_exec', side_effect=fake_process):
                        self.assertEqual(client.post('/api/cluster', headers=headers, json={'limit': 100, 'ignore_attachments': True}).status_code, 200)
                        self.assertEqual(client.post('/api/cluster', headers=headers, json={'limit': 100}).status_code, 409)
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            summary = client.get('/api/status', headers=headers).json()['topics']
                            if summary['status'] != 'running':
                                break
                            time.sleep(.03)
                        self.assertEqual(summary['status'], 'done')
                        self.assertEqual(summary['processed'], 5)
                        previews = client.get('/api/topics', headers=headers).json()['groups'][0]['examples']
                        self.assertEqual(len(previews), 5)
                        self.assertEqual(list((Path(directory) / 'topic-runs').iterdir()), [])
                    current_run = client.get('/api/topics', headers=headers).json()['run_id']
                    self.assertEqual(client.post('/api/later', headers=headers, json={'run_id': rid, 'topic': 0}).status_code, 409)
                    self.assertEqual(client.post('/api/later', json={'run_id': current_run, 'topic': 0}).status_code, 403)
                    self.assertEqual(client.post('/api/later', headers=headers, json={'run_id': current_run, 'topic': -1}).status_code, 422)
                    self.assertEqual(client.post('/api/later', headers=headers, json={'run_id': current_run, 'topic': 0, 'ids': ['arbitrary']}).status_code, 422)
                    response = client.post('/api/later', headers=headers, json={'run_id': current_run, 'topic': 0})
                    self.assertEqual(response.json()['later']['moved'], 5)
                    self.assertEqual(writes[0]['ids'], ['fixture-id'] + ['additional-' + str(index) for index in range(4)])
                    self.assertEqual(writes[0]['removeLabelIds'], ['CATEGORY_UPDATES'])
                    self.assertEqual(client.get('/api/topics', headers=headers).json()['groups'][0]['later']['status'], 'done')
                    self.assertNotIn('fixture-id', response.text)
                    before = client.get('/api/topics', headers=headers).json()['groups'][0]
                    originals = [store.original('fixture-account', mid) for mid in writes[0]['ids']]
                    payload = {'run_id': current_run, 'topic': 0}
                    self.assertEqual(client.post('/api/trash', json=payload).status_code, 403)
                    self.assertEqual(client.post('/api/trash', headers=headers, json={**payload, 'run_id': rid}).status_code, 409)
                    self.assertEqual(client.post('/api/trash', headers=headers, json={**payload, 'ids': ['arbitrary']}).status_code, 422)
                    self.assertEqual(client.post('/api/trash', headers=headers, json={**payload, 'topic': -1}).status_code, 422)
                    self.assertEqual(client.post('/api/trash', headers=headers, json=payload).json(), {'started': True})
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        deletion = client.get('/api/status', headers=headers).json()['trash']
                        if deletion['status'] != 'running':
                            break
                        time.sleep(.03)
                    self.assertEqual(deletion['status'], 'done')
                    self.assertEqual(deletion['moved'], 5)
                    self.assertEqual(set(trash_writes), set(writes[0]['ids']))
                    after = client.get('/api/topics', headers=headers).json()['groups'][0]
                    self.assertEqual(after['trash']['moved'], 5)
                    self.assertEqual({k: v for k, v in after.items() if k != 'trash'},
                                     {k: v for k, v in before.items() if k != 'trash'})
                    self.assertEqual([store.original('fixture-account', mid) for mid in writes[0]['ids']], originals)
                    provider_account[0] = 'different-account'
                    self.assertEqual(client.post('/api/trash', headers=headers, json=payload).status_code, 409)
                    self.assertEqual(len(trash_writes), 5)
                    provider_account[0] = 'fixture-account'
                    client.post('/api/refresh', headers=headers)
                    provider_account[0] = 'different-account'
                    self.assertEqual(client.post('/api/later', headers=headers, json={'run_id': current_run, 'topic': 0}).status_code, 409)
                    self.assertEqual(len(writes), 1)


    def test_public_proxy_exposes_only_explicit_example_fields(self):
        real_client = httpx.AsyncClient
        def provider(request):
            return httpx.Response(200, json={'status': 'done', 'groups': [{'topic': -1, 'count': 2,
                'keywords': [], 'members': ['private-id'], 'examples': [{'subject': 'Fixture', 'text': 'Synthetic text',
                    'received': 1, 'token': 'secret-token', 'id': 'private-id'}] * 5}], 'account': 'private-account'})
        def fake_client(*args, **kwargs):
            return real_client(*args, **kwargs, transport=httpx.MockTransport(provider))
        with patch.dict('os.environ', {'GMAIL_SNAPSHOT_CAPABILITY': 'synthetic-capability'}), patch('snapshot.ui_server.httpx.AsyncClient', side_effect=fake_client):
            with TestClient(create_ui(), base_url='http://127.0.0.1:8766') as client:
                self.assertEqual(client.get('/api/snapshot/topics').status_code, 403)
                client.get('/')
                response = client.get('/api/snapshot/topics')
                self.assertEqual(response.status_code, 200)
                self.assertIn('Synthetic text', response.text)
                self.assertEqual(len(response.json()['groups'][0]['examples']), 5)
                for secret in ('private-id', 'private-account', 'secret-token'):
                    self.assertNotIn(secret, response.text)


if __name__ == '__main__':
    unittest.main()
