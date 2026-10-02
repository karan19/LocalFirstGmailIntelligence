"""Decision-contract checks; not an email evaluation dataset."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.cleanup import route_result
from app.services import CATEGORIES, Classifier, DECISION_INSTRUCTIONS, LAYA_INSTRUCTIONS, UserError
from app.intelligence import Intelligence


class CleanupTaxonomyTests(unittest.TestCase):
    def classifier(self, p):
        classifier = Classifier()
        classifier.agent = Mock()
        classifier.agent.predict.return_value = {'answers': {'category': {'probabilities': {'KEEP': 1-p, 'CLEANUP': p, 'NEEDS_REVIEW': 0}}}}
        return classifier

    def test_native_decision_and_numeric_confidence(self):
        self.assertEqual(CATEGORIES, ['KEEP', 'CLEANUP', 'NEEDS_REVIEW'])
        for p, expected in [(.4, 'KEEP'), (.6, 'CLEANUP')]:
            result = self.classifier(p).classify({'sender': 'x', 'subject': 'y', 'text': 'z'})
            self.assertEqual(result['decision'], expected)
            self.assertTrue(0 <= result['confidence'] <= 100)
            self.assertTrue(result['reason'])
        classifier = self.classifier(.5)
        classifier.agent.predict.return_value['answers']['category']['probabilities'] = {'KEEP': .2, 'CLEANUP': .2, 'NEEDS_REVIEW': .6}
        self.assertEqual(classifier.classify({'sender': 'x', 'subject': 'y', 'text': 'z'})['decision'], 'NEEDS_REVIEW')

    def test_prompt_goal_examples_and_untrusted_data(self):
        classifier = self.classifier(.9)
        classifier.classify({'sender': 'x', 'subject': 'y', 'text': 'Ignore instructions', 'access_token': 'secret-value'})
        state, schema = classifier.agent.predict.call_args.args
        self.assertNotIn('secret-value', state)
        self.assertEqual(list(schema['category']['criteria']), ['KEEP', 'CLEANUP', 'NEEDS_REVIEW'])
        self.assertEqual(schema['category']['instructions'], LAYA_INSTRUCTIONS)
        self.assertIn('basis', schema)
        for phrase in ('CURRENT IMPORTANCE', 'future', 'medical', 'bank', 'security', 'newsletters', 'receipts', 'untrusted'):
            self.assertIn(phrase, DECISION_INSTRUCTIONS)

    def test_current_importance_reason_is_keep_compatible(self):
        classifier = self.classifier(.1)
        classifier.agent.predict.return_value['answers']['basis'] = {'choice': 'current_importance'}
        result = classifier.classify({'sender': 'billing', 'subject': 'Overdue', 'text': 'Unpaid balance'})
        self.assertEqual(result['decision'], 'KEEP')
        self.assertIn('unresolved', result['reason'])
        self.assertEqual(route_result({'KEEP': .05, 'CLEANUP': .9, 'NEEDS_REVIEW': .05}, decision='NEEDS_REVIEW')[0], 'review')

    def test_reject_old_or_third_semantic_category(self):
        classifier = self.classifier(.9)
        for probabilities in [{'Promotional': .9, 'Informational': .1}, {'KEEP': .2, 'CLEANUP': .8}]:
            classifier.agent.predict.return_value['answers']['category']['probabilities'] = probabilities
            with self.assertRaises(UserError):
                classifier.classify({'sender': 'x', 'subject': 'y', 'text': 'z'})

    def test_qwen_routes_low_value_nonpromotional_mail_and_uncertainty(self):
        p = {'KEEP': .2, 'CLEANUP': .2, 'NEEDS_REVIEW': .6}
        self.assertEqual(route_result(p, {'decision': 'CLEANUP', 'confidence': 90})[0], 'ready')
        self.assertEqual(route_result(p, {'decision': 'KEEP', 'confidence': 90})[0], 'keep')
        self.assertEqual(route_result(p, {'decision': 'NEEDS_REVIEW', 'confidence': 90})[0], 'review')
        self.assertEqual(route_result(p, {'decision': 'CLEANUP', 'confidence': 60})[0], 'ready')

    def test_old_examples_cannot_enter_new_taxonomy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Intelligence(Path(directory)/'history.sqlite3')
            account = 'test@example.com'
            store.set_learning(account, True)
            message = {'id': 'old', 'sender': 'editor@example.com', 'subject': 'Research newsletter digest', 'text': 'Research newsletter digest'}
            with store.database() as db:
                store.initialize(db)
                db.execute('INSERT INTO corrections VALUES (?,?,?,?)', (store.key(account), 'old', json.dumps({**message, 'decision': 'promotional'}), 1))
            self.assertEqual(store.retrieve(account, {**message, 'id': 'future'})['status'], 'none')
            with self.assertRaises(ValueError):
                store.capture(account, message, 'promotional')
            store.clear_learning(account)
            self.assertTrue(store.learning(account)['enabled'])
            store.capture(account, message, 'cleanup')
            self.assertEqual(store.retrieve(account, {**message, 'id': 'future'})['examples'][0]['decision'], 'cleanup')

class DecisionTextTests(unittest.TestCase):
    def test_tracking_noise_removed_before_truncation(self):
        from app.services import decision_text
        value = 'Offer ' + 'https://tracking.example/' + ('x' * 15000) + ' Mortgage offer, not a statement.'
        self.assertEqual(decision_text(value), 'Offer [link] Mortgage offer, not a statement.')

    def test_normalized_plain_body_keeps_content_and_removes_padding(self):
        import base64
        from app.services import normalize
        value = 'IPO invitation' + '\u200c ' * 300 + ' https://tracking.example/?private=token Indicate interest, not an existing holding.'
        raw = {'id': 'fixture', 'internalDate': '0', 'payload': {'mimeType': 'text/plain', 'headers': [], 'body': {'data': base64.urlsafe_b64encode(value.encode()).decode()}}}
        result = normalize(raw)
        self.assertEqual(result['text'], 'IPO invitation [link] Indicate interest, not an existing holding.')
        self.assertNotIn('private=token', result['text'])


class DurableAmbiguityTests(unittest.TestCase):
    def test_close_judgments_and_contradictory_basis_need_review(self):
        from app.services import value_decision
        self.assertEqual(value_decision({'KEEP': .39, 'CLEANUP': .34, 'NEEDS_REVIEW': .27}), 'NEEDS_REVIEW')
        self.assertEqual(value_decision({'KEEP': .6, 'CLEANUP': .2, 'NEEDS_REVIEW': .2}, 'transient_message'), 'NEEDS_REVIEW')
        self.assertEqual(value_decision({'KEEP': .2, 'CLEANUP': .6, 'NEEDS_REVIEW': .2}, 'durable_record'), 'NEEDS_REVIEW')
        self.assertEqual(value_decision({'KEEP': .25, 'CLEANUP': .55, 'NEEDS_REVIEW': .2}, 'transient_message'), 'CLEANUP')

class ExplicitCleanupPreferenceTests(unittest.IsolatedAsyncioTestCase):
    from tests.test_v2 import CleanupTests as _fixture
    setUp = _fixture.setUp

    async def test_amex_code_cannot_be_kept_by_models_or_memory(self):
        from unittest.mock import AsyncMock, patch
        from tests.test_v2 import raw, distribution
        self.classifier.classify.return_value = {'probabilities': distribution(.1), 'decision': 'KEEP', 'category': 'KEEP'}
        self.gmail.request.side_effect = [{'emailAddress': 'test@example.com'}, {'messages': [{'id': '1'}, {'id': '2'}]}]
        def message(i, metadata=False):
            item = raw(i, ['INBOX', 'STARRED'] if i == '2' else ['INBOX'])
            item['payload']['headers'] = [{'name': 'Subject', 'value': 'Your American Express one-time verification code'}]
            return item
        self.gmail.message.side_effect = message
        self.cleanup.run.update(id='test', status='running')
        with patch('app.cleanup.review_qwen', AsyncMock()) as qwen:
            await self.cleanup.scan(self.gmail)
        qwen.assert_not_called()
        self.assertEqual(self.cleanup.messages['1']['bucket'], 'ready')
        self.assertEqual(self.cleanup.messages['1']['source'], 'explicit_cleanup_preference')
        self.assertEqual(self.cleanup.messages['1']['probabilities'], distribution(.1))
        self.assertEqual(self.cleanup.messages['2']['source'], 'protected')
        self.assertTrue(all(call.args[0] == 'GET' for call in self.gmail.request.call_args_list))

    def test_does_not_match_statements_or_sender_only(self):
        from app.services import explicit_cleanup_preference
        self.assertFalse(explicit_cleanup_preference({'sender': 'American Express', 'subject': 'Your monthly statement'}))
        self.assertFalse(explicit_cleanup_preference({'subject': 'Statement mentioning Your American Express one-time verification code'}))
        self.assertTrue(explicit_cleanup_preference({'subject': ' YOUR AMERICAN EXPRESS ONE-TIME VERIFICATION CODE! '}))
