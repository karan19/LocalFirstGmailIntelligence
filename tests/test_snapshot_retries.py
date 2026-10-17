"""Synthetic HTTP failures; no live Gmail or credentials."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from snapshot.errors import diagnostic, message
from snapshot.private_service import GmailClient


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def client(self, provider, refresh=None):
        http = httpx.AsyncClient(transport=httpx.MockTransport(provider), headers={'Authorization': 'Bearer old'})
        self.addAsyncCleanup(http.aclose)
        with patch('snapshot.private_service.httpx.AsyncClient', return_value=http):
            gmail = GmailClient('old', refresh=refresh)
            gmail.quota.wait = AsyncMock()
            gmail.quota.cooldown = lambda seconds: None
            return gmail

    async def test_retries_403_rate_limit_and_honors_retry_after(self):
        calls = []
        def provider(request):
            calls.append(request)
            if len(calls) < 3:
                return httpx.Response(403, headers={'Retry-After': '2'}, json={'error': {'errors': [{'reason': 'userRateLimitExceeded'}]}})
            return httpx.Response(200, json={'id': 'synthetic'})
        gmail = await self.client(provider)
        with patch('snapshot.private_service.asyncio.sleep', new_callable=AsyncMock) as sleep, patch('snapshot.private_service.random.random', return_value=0):
            self.assertEqual(await gmail.request('messages/synthetic'), {'id': 'synthetic'})
            self.assertEqual([call.args[0] for call in sleep.await_args_list], [2, 2])
        self.assertEqual(len(calls), 3)
        self.assertEqual(gmail.quota.wait.await_count, 3)

    async def test_refreshes_expired_token_and_retries_same_request(self):
        seen = []
        def provider(request):
            seen.append(request.headers['Authorization'])
            return httpx.Response(401 if seen[-1] == 'Bearer old' else 200, json={})
        refresh = AsyncMock(return_value='new')
        gmail = await self.client(provider, refresh)
        await gmail.request('profile')
        self.assertEqual(seen, ['Bearer old', 'Bearer new'])
        refresh.assert_awaited_once()

    async def test_failed_reauthorization_is_bounded(self):
        refresh = AsyncMock(return_value='new')
        gmail = await self.client(lambda request: httpx.Response(401, json={}), refresh)
        with self.assertRaises(httpx.HTTPStatusError) as caught:
            await gmail.request('profile')
        self.assertEqual(diagnostic(caught.exception)['code'], 'authorization')
        refresh.assert_awaited_once()

    async def test_daily_quota_is_not_retried_and_error_text_stays_private(self):
        gmail = await self.client(lambda request: httpx.Response(403, json={'error': {'message': 'PRIVATE BODY AND TOKEN', 'errors': [{'reason': 'dailyLimitExceeded'}]}}))
        with patch('snapshot.private_service.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await gmail.request('messages/synthetic')
            sleep.assert_not_awaited()
        result = diagnostic(caught.exception)
        self.assertEqual(result, {'code': 'daily_limit', 'http_status': 403})
        self.assertNotIn('PRIVATE', json.dumps(result) + message(result))

    async def test_exhausted_server_retries_preserve_http_status(self):
        gmail = await self.client(lambda request: httpx.Response(503))
        with patch('snapshot.private_service.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await gmail.request('messages/synthetic')
            self.assertEqual(sleep.await_count, 5)
        self.assertEqual(diagnostic(caught.exception), {'code': 'gmail_unavailable', 'http_status': 503})

    async def test_long_retry_after_returns_actionable_rate_error(self):
        gmail = await self.client(lambda request: httpx.Response(429, headers={'Retry-After': '3600'}))
        with patch('snapshot.private_service.asyncio.sleep', new_callable=AsyncMock) as sleep:
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await gmail.request('messages/synthetic')
            sleep.assert_not_awaited()
        self.assertEqual(diagnostic(caught.exception)['code'], 'rate_limit')


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_are_paced_by_quota_cost(self):
        import asyncio
        from snapshot.quota import GmailQuota
        now = [0.0]
        delays = []
        async def sleep(delay):
            delays.append(delay)
            now[0] += delay
        quota = GmailQuota(clock=lambda: now[0], sleep=sleep)
        await asyncio.gather(*(quota.wait('GET', 'messages/fixture') for _ in range(4)))
        self.assertEqual(delays, [.25, .25, .25])
        await quota.wait('POST', 'messages/batchModify')
        await quota.wait('GET', 'messages/fixture')
        self.assertEqual(delays[-1], .625)

    async def test_rate_limit_cooldown_applies_to_other_requests(self):
        from snapshot.quota import GmailQuota
        now = [0.0]
        async def sleep(delay):
            now[0] += delay
        quota = GmailQuota(clock=lambda: now[0], sleep=sleep)
        await quota.wait('GET', 'messages/one')
        quota.cooldown(10)
        await quota.wait('GET', 'messages/two')
        self.assertEqual(now[0], 10)
