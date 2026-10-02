"""Opt-in developer check: real local models, synthetic mail, temporary storage, no Gmail.
Run from the repo: PYTHONPATH=. .venv/bin/python scripts/validate_v4_learning.py
"""
import asyncio, json, tempfile, base64
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from fastapi import FastAPI
from app.cleanup import Cleanup, Decision, review_qwen, route_result
from app.history import ReviewHistory
from app.services import Classifier

async def main():
    with tempfile.TemporaryDirectory() as directory:
        cleanup = Cleanup(FastAPI(), Mock(), Classifier(), lambda: {}, AsyncMock(), lambda: False)
        cleanup.history = ReviewHistory(Path(directory)/'reviews.sqlite3')
        account = 'controlled-local-validation@example.test'
        text = 'Weekly research digest. Subscribe to our premium research newsletter for twenty dollars per month. Upgrade today for exclusive scientific papers, expert analysis, and members-only research reports.'
        previous = {'id': 'example-week-one', 'sender': 'Research editor <editor@research.example>', 'subject': 'Weekly premium research digest', 'text': text, 'received': 1, 'protected': [], 'bucket': 'ready', 'source': 'laya_cleanup'}
        cleanup.run.update(id='explicit-review', status='done', account=account)
        cleanup.messages = {previous['id']: previous}
        cleanup.intelligence.set_learning(account, True)
        await cleanup.decide('explicit-review', previous['id'], Decision(decision='keep'))
        cleanup.reset()
        cleanup.run.update(id='held-out-validation', status='running')
        gmail = Mock()
        gmail.close = AsyncMock()
        gmail.request = AsyncMock(side_effect=[{'emailAddress': account}, {'messages': [{'id': 'example-week-two'}]}])
        gmail.message = AsyncMock(return_value={'id': 'example-week-two', 'labelIds': ['INBOX'], 'internalDate': '2', 'payload': {'mimeType': 'text/plain', 'body': {'data': base64.urlsafe_b64encode(text.replace('twenty dollars', 'twenty-five dollars').encode()).decode()}, 'headers': [{'name': 'From', 'value': previous['sender']}, {'name': 'Subject', 'value': previous['subject']+' · next edition'}]}})
        await cleanup.scan(gmail)
        result = cleanup.messages.get('example-week-two', {})
        output = {k: result.get(k) for k in ('bucket', 'source', 'probabilities', 'qwen', 'memory')}
        baseline_qwen = await review_qwen(result)
        baseline_bucket = route_result(result['probabilities'], baseline_qwen)[0]
        output['baseline_without_memory'] = {'bucket': baseline_bucket, 'qwen': baseline_qwen}
        output['dashboard'] = cleanup.intelligence.dashboard(account)
        print(json.dumps(output))
        assert result.get('source') == 'memory_qwen', cleanup.run
        assert result['bucket'] == 'keep', result.get('qwen')
        assert baseline_bucket == 'ready', baseline_qwen
        assert cleanup.intelligence.learning(account)['count'] == 1
        assert output['dashboard']['total_runs'] == 1
        print('Real Laya + Qwen held-out local correction validation passed. Isolated temporary data removed.')

asyncio.run(main())
