"""Counts-only download diagnostics; never serialize exception text."""
import sqlite3

import httpx


def diagnostic(error):
    code, status = 'unexpected', None
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if error.request.url.host == 'oauth2.googleapis.com' or status == 401:
            code = 'authorization'
        elif status == 429:
            code = 'rate_limit'
        elif status in (500, 502, 503, 504):
            code = 'gmail_unavailable'
        elif status == 403:
            code = 'permission'
            try:
                reasons = {item.get('reason') for item in error.response.json().get('error', {}).get('errors', [])}
                if reasons & {'rateLimitExceeded', 'userRateLimitExceeded'}:
                    code = 'rate_limit'
                elif 'dailyLimitExceeded' in reasons:
                    code = 'daily_limit'
            except (ValueError, TypeError, AttributeError):
                pass
        elif status == 404:
            code = 'not_found'
        else:
            code = 'gmail_request'
    elif isinstance(error, httpx.TransportError):
        code = 'network'
    elif isinstance(error, (OSError, sqlite3.Error)):
        code = 'storage'
    elif isinstance(error, ValueError):
        code = 'invalid_response'
    return {'code': code, 'http_status': status}


MESSAGES = {
    'authorization': 'Gmail authorization could not be refreshed. Reconnect Gmail, then resume.',
    'rate_limit': 'Gmail rate limiting persisted after retries. Wait a little, then resume.',
    'daily_limit': 'The Google project daily API limit was reached. Resume after the quota resets.',
    'permission': 'Gmail denied this request. Check account permissions or reconnect, then resume.',
    'gmail_unavailable': 'Gmail remained unavailable after retries. Resume later.',
    'not_found': 'A Gmail resource was not found. Refresh your connection and resume.',
    'gmail_request': 'Gmail rejected a request. Check the HTTP error code before retrying.',
    'network': 'The connection to Gmail failed after retries. Check the network, then resume.',
    'storage': 'The service could not access or write its local storage. Check permissions and free disk space.',
    'invalid_response': 'The service received an unexpected Gmail response. Resume to retry.',
    'unexpected': 'An unexpected download failure occurred. Resume to retry.',
}


def message(value):
    text = MESSAGES.get(value.get('code'), MESSAGES['unexpected'])
    status = value.get('http_status')
    return text + (f' (HTTP {status})' if type(status) is int else '')
