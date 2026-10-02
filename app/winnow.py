"""Local Winnow typed-decision adapter; never sends mail to a hosted provider."""
import math
import os
import time
import httpx
from .services import DECISION_INSTRUCTIONS, DECISION_CRITERIA, UserError, decision_text

URL = os.environ.get('WINNOW_LOCAL_URL','http://127.0.0.1:8091')
from urllib.parse import urlparse
if urlparse(URL).hostname != '127.0.0.1':raise ValueError('Winnow must be local')
WINNOW_CATEGORIES = ('KEEP', 'CLEANUP')
# Single binary retention question. Uncertainty is routed by Cleanup probability.
WINNOW_INSTRUCTIONS = DECISION_INSTRUCTIONS.replace('KEEP\nCLEANUP\nNEEDS_REVIEW', 'KEEP\nCLEANUP')
a = WINNOW_INSTRUCTIONS.index('4. CONFLICTING OR UNCLEAR CASES')
b = WINNOW_INSTRUCTIONS.index('CORE QUESTION:', a)
WINNOW_INSTRUCTIONS = WINNOW_INSTRUCTIONS[:a] + WINNOW_INSTRUCTIONS[b:]
WINNOW_INSTRUCTIONS = WINNOW_INSTRUCTIONS.replace('If genuinely unclear -> NEEDS_REVIEW', 'For unclear cases choose the more likely retention outcome; the application sends intermediate Cleanup scores to Qwen.')

def cleanup_decision(probability):
    return 'CLEANUP' if probability > .6 else 'KEEP' if probability < .2 else 'NEEDS_REVIEW'


class WinnowClassifier:
    def classify(self, message):
        started = time.monotonic()
        payload = {'model': 'Winnow-12B', 'state': {k: decision_text(message[k]) for k in ('sender', 'subject', 'text')},
                   'questions': {'category': {'type': 'choice', 'instructions': WINNOW_INSTRUCTIONS, 'criteria': {k:DECISION_CRITERIA[k] for k in WINNOW_CATEGORIES}}}}
        try:
            with httpx.Client(timeout=180, trust_env=False) as client:
                response = client.post(URL + '/v1/systemone', json=payload)
                response.raise_for_status()
                answers = response.json()['answers']
            probabilities = answers['category']['probabilities']
            if set(probabilities) != set(WINNOW_CATEGORIES) or any(isinstance(p, bool) or not isinstance(p, (int,float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()) or abs(sum(probabilities.values())-1) > .001:
                raise ValueError('Invalid distribution')
            confidence = answers['category']['confidence']
            if isinstance(confidence, bool) or not isinstance(confidence, (int,float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError('Invalid confidence')
            decision = cleanup_decision(probabilities['CLEANUP'])
            reason = {'KEEP': 'Cleanup score below 20%: retain.', 'CLEANUP': 'Cleanup score above 60%: eligible for confirmed Trash.', 'NEEDS_REVIEW': 'Cleanup score from 20% to 60%: forward to Qwen.'}[decision]
            return {'probabilities':probabilities, 'decision':decision, 'category':decision, 'confidence':round(confidence*100,1), 'reason':reason, 'elapsed_seconds':round(time.monotonic()-started,3)}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise UserError('Winnow is unavailable or returned an invalid decision. Start the local Winnow server and retry.') from None

    def ready(self):
        try:
            return httpx.get(URL+'/health',timeout=2,trust_env=False).status_code == 200
        except httpx.HTTPError:
            return False
