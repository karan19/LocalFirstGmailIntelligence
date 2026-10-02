"""Important is eligible; Starred is excluded even at write-time."""
import unittest
from unittest.mock import AsyncMock, patch
import tests.test_v2 as v2
from app.cleanup import PROTECTED, Selection, ConfirmedSelection

class StarredOnlyTests(unittest.IsolatedAsyncioTestCase):
    setUp = v2.CleanupTests.setUp

    async def test_important_label_does_not_block_confirmed_trash(self):
        self.assertEqual(PROTECTED, {'STARRED'})
        self.ready()
        prepared = await self.cleanup.prepare('run', Selection(ids=['1']))
        self.gmail.message.side_effect = None
        self.gmail.message.return_value = v2.raw(1,['INBOX','IMPORTANT'])
        with patch.object(self.cleanup,'authorize',AsyncMock(return_value=self.gmail)):
            result = await self.cleanup.trash('run',ConfirmedSelection(ids=['1'],confirmation=prepared['confirmation']))
        self.assertEqual(result['succeeded'],1)
        self.assertEqual(self.cleanup.messages['1']['trash_status'],'trashed')

    ready = v2.CleanupTests.ready
