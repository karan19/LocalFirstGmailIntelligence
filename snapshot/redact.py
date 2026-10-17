"""Local-only experimental redaction. No network, Keychain, or mailbox access on import."""
import argparse
import base64
from collections import Counter
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

VERSION = 1
RULES = [
    ('URL', re.compile(r'https?://[^\s<>"\)]+|www\.[^\s<>"\)]+', re.I)),
    ('EMAIL', re.compile(r'[\w.!#$%&\x27*+/=?^`{|}~-]+@[\w.-]+\.[a-z]{2,}', re.I)),
    ('PHONE', re.compile(r'(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)')),
    ('ACCOUNT', re.compile(r'\b(?:account|order|invoice|customer|reference|tracking)\s*(?:number|no\.?|id|#)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9-]{4,})\b', re.I)),
]


class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if tag in ('br', 'p', 'div', 'li', 'tr') and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if tag in ('p', 'div', 'li', 'tr', 'a') and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract(original):
    raw = original['raw']
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    headers = [str(message[k]) for k in ('From', 'To', 'Cc', 'Reply-To') if message[k]]
    names = [name for name, _ in getaddresses(headers) if name]
    body = message.get_body(preferencelist=('plain', 'html'))
    text = body.get_content() if body is not None else ''
    if not isinstance(text, str):
        raise ValueError('Unsupported mail text')
    if body is not None and body.get_content_subtype() == 'html':
        parser = HtmlText()
        parser.feed(text)
        text = ''.join(parser.parts)
    attachments = sum(1 for part in message.walk() if part.get_content_disposition() == 'attachment'
                      or (part.get_filename() and not (part.get_content_disposition() == 'inline'
                                                     and part.get_content_maintype() == 'image')))
    return 'Subject: ' + str(message.get('Subject', '')) + '\n\n' + text, names, attachments


def redact(text, document, names=()):
    spans = []
    for label, pattern in RULES:
        for match in pattern.finditer(text):
            start, end = match.span(1) if label == 'ACCOUNT' else match.span()
            spans.append((start, end, label, 0))
    for name in names:
        if len(name.strip()) < 2:
            continue
        for match in re.finditer(r'(?<!\w)' + re.escape(name.strip()) + r'(?!\w)', text, re.I):
            spans.append((*match.span(), 'PERSON', 1))
    for entity in document.ents:
        if entity.label_ in ('PERSON', 'ORG', 'GPE', 'LOC', 'FAC'):
            spans.append((entity.start_char, entity.end_char, entity.label_, 2))
    # Longest rule spans win; entities cannot partially expose an address or URL.
    accepted = []
    for span in sorted(spans, key=lambda s: (s[3], -(s[1] - s[0]), s[0])):
        if not any(span[0] < other[1] and other[0] < span[1] for other in accepted):
            accepted.append(span)
    output, cursor, replacements, counts = [], 0, {}, Counter()
    for start, end, label, _ in sorted(accepted):
        key = (label, text[start:end].casefold())
        if key not in replacements:
            counts[label] += 1
            replacements[key] = f'[{label}_{counts[label]}]'
        output.extend((text[cursor:start], replacements[key]))
        cursor = end
    output.append(text[cursor:])
    return ''.join(output), dict(counts)


def load_model():
    import spacy
    model = spacy.load('en_core_web_sm', enable=['tok2vec', 'ner'])
    if 'ner' not in model.pipe_names:
        raise ValueError('A trained name detector is required')
    return model


def main():
    parser = argparse.ArgumentParser(description='Private local redaction review; never sends mail externally')
    parser.add_argument('--directory', type=Path, default=Path.home() / 'CleanUpFolderService')
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--json-summary', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--job-id', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100000:
        parser.error('Limit must be between 1 and 100000')
    try:
        from .private_service import private_path
        directory = private_path(str(args.directory))
        path = directory / 'mailbox.sqlite3'
        if path.is_symlink():
            raise ValueError('Unsafe database path')
        model = load_model()
        # Read-only: never changes originals or download progress.
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            latest = (db.execute('SELECT account FROM jobs WHERE id=?', (args.job_id,)).fetchone() if args.job_id
                      else db.execute('SELECT account FROM jobs ORDER BY created DESC LIMIT 1').fetchone())
            if not latest:
                raise ValueError('No stored download')
            rows = db.execute('SELECT original FROM messages WHERE account=? ORDER BY received DESC,id LIMIT ?', (latest[0], args.limit)).fetchall()
        finally:
            db.close()
        review = directory / ('redaction-review-' + secrets.token_hex(6))
        review.mkdir(mode=0o700)
        started = time.perf_counter()
        def inputs():
            for (value,) in rows:
                text, names, attachments = extract(json.loads(value))
                yield text, (text, names, attachments)
        for number, (doc, context) in enumerate(model.pipe(inputs(), as_tuples=True, batch_size=32), 1):
            text, names, attachments = context
            redacted, counts = redact(text, doc, names)
            value = {'version': VERSION, 'text': redacted, 'detections': counts,
                     'attachments_omitted': attachments, 'review_required': True}
            fd = os.open(review / f'message-{number:04d}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
        elapsed = time.perf_counter() - started
        if args.json_summary:
            print(json.dumps({'processed': len(rows), 'seconds': round(elapsed, 2)}))
        else:
            print(f'Prepared {len(rows)} private review copies in {elapsed:.2f}s. Originals unchanged. No external calls.')
            print(f'Local review folder: {review}')
            print('Review locally for missed identities. Output remains private and is not approved for external scoring.')
    except Exception:
        # Do not leak email fragments or paths from parser/model tracebacks.
        parser.exit(1, 'Redaction failed. Check local model installation and private storage access in your trusted terminal. No mail was sent.\n')


if __name__ == '__main__':
    main()
