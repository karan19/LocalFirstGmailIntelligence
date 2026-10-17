"""Pace Gmail requests below the current per-user quota, including retries."""
import asyncio
import time


class GmailQuota:
    def __init__(self, clock=time.monotonic, sleep=asyncio.sleep):
        self.clock, self.sleep = clock, sleep
        self.lock = asyncio.Lock()
        self.next_start = 0
        self.blocked_until = 0

    def cooldown(self, seconds):
        self.blocked_until = max(self.blocked_until, self.clock() + seconds)

    async def wait(self, method, path):
        # Gmail messages.get now costs 20 units; batchModify costs 50.
        # Budget 80 units/sec (4,800/min), leaving headroom below 6,000/min.
        cost = 50 if path == 'messages/batchModify' else 20 if path.startswith('messages/') and (method == 'GET' or path.endswith('/trash')) else 5
        async with self.lock:
            while True:
                delay = max(self.next_start, self.blocked_until) - self.clock()
                if delay <= 0:
                    break
                await self.sleep(delay)
            self.next_start = self.clock() + cost / 80
