"""Offline BERTopic analysis of originals. No mailbox access on import."""
import argparse
from collections import Counter
from html import unescape
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .redact import extract

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIRECTORY = ROOT / '.local/models/all-MiniLM-L6-v2'


def clean_padding(text):
    # Automated messages pad their preheaders with entities and invisible marks.
    # Clean before truncating so meaningful body text retains the input budget.
    for _ in range(2):
        text = unescape(text)
    text = re.sub(r'[\u200b-\u200f\u2060\ufeff\u00ad]', '', text)
    text = re.sub(r'[^\S\n]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def prepare(original):
    text, _, _ = extract(original)
    subject, _, body = text.partition('\n\n')
    # Remove quoted reply lines; keep original content and identifiers locally.
    body = '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('>'))
    body = re.split(r'(?im)^On .+ wrote:\s*$', body, maxsplit=1)[0]
    subject = re.sub(r'(?i)^Subject:\s*(?:(?:re|fw|fwd):\s*)*', '', subject).strip()
    subject = clean_padding(subject)
    text = clean_padding(subject + '\n' + body)
    return subject, text[:12000]


def select_records(records, *, ignore_attachments=False):
    usable = []
    excluded_attachments = 0
    for record in records:
        try:
            if ignore_attachments and extract(record['original'])[2]:
                excluded_attachments += 1
                continue
            subject, text = prepare(record['original'])
            if len(re.findall(r'\w+', text)) < 3:
                continue
            usable.append({**record, 'subject': subject, 'text': text})
        except Exception:
            continue
    return usable, excluded_attachments


def encode_batches(encoder, texts, progress, batch_size=32):
    import numpy as np
    embeddings = None
    for offset in range(0, len(texts), batch_size):
        batch = encoder.encode(texts[offset:offset + batch_size], batch_size=batch_size,
                               show_progress_bar=False, normalize_embeddings=True)
        if embeddings is None:
            embeddings = np.empty((len(texts), batch.shape[1]), dtype=np.float32)
        embeddings[offset:offset + len(batch)] = batch
        progress('embedding', offset + len(batch), len(texts))
    return embeddings


def fit(records, model_directory=MODEL_DIRECTORY, *, ignore_attachments=False, progress=None):
    progress = progress or (lambda phase, completed, total: None)
    # Local files only, with telemetry and Hub access disabled during inference.
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
    import numpy as np
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sentence_transformers import SentenceTransformer
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP
    usable, excluded_attachments = select_records(records, ignore_attachments=ignore_attachments)
    if len(usable) < 5:
        raise ValueError('At least five readable messages are required')
    progress('loading_model', 0, len(usable))
    encoder = SentenceTransformer(str(model_directory), local_files_only=True, trust_remote_code=False, device='cpu')
    texts = [row['text'] for row in usable]
    embeddings = encode_batches(encoder, texts, progress)
    progress('grouping', len(usable), len(usable))
    model = BERTopic(embedding_model=None,
        umap_model=UMAP(n_neighbors=min(15, len(usable)-1), n_components=min(5, len(usable)-2),
                        metric='cosine', random_state=42, init='random'),
        hdbscan_model=HDBSCAN(min_cluster_size=min(5, max(3, len(usable)//10)), min_samples=2,
                             metric='euclidean', cluster_selection_method='eom', prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words='english', ngram_range=(1, 2)),
        top_n_words=8, calculate_probabilities=False, verbose=False)
    assignments, _ = model.fit_transform(texts, embeddings=embeddings)
    counts = Counter(int(topic) for topic in assignments)
    groups = []
    for topic, count in sorted(counts.items(), key=lambda pair: (pair[0] == -1, -pair[1], pair[0])):
        members = [index for index, value in enumerate(assignments) if value == topic]
        center = embeddings[members].mean(axis=0)
        ranked = sorted(members, key=lambda i: float(np.dot(embeddings[i], center)), reverse=True)
        groups.append({'topic': topic, 'count': count,
                       'keywords': [word for word, _ in (model.get_topic(topic) or []) if word][:8],
                       'members': [usable[i]['id'] for i in members],
                       'examples': [usable[i]['id'] for i in ranked[:3]]})
    return {'model': 'sentence-transformers/all-MiniLM-L6-v2', 'preparation_version': 2,
            'min_cluster_size': min(5, max(3, len(usable)//10)), 'processed': len(usable),
            'skipped': len(records)-len(usable)-excluded_attachments,
            'ignore_attachments': ignore_attachments, 'excluded_attachments': excluded_attachments, 'groups': groups}


def main():
    parser = argparse.ArgumentParser(description='Local original-email topic discovery')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--ignore-attachments', action='store_true')
    args = parser.parse_args()
    try:
        if not 5 <= args.limit <= 100000 or not re.fullmatch(r'[a-f0-9]{32}', args.run_id):
            raise ValueError('Invalid request')
        from .private_service import private_path
        directory = private_path(str(args.directory))
        database = directory / 'mailbox.sqlite3'
        if database.is_symlink():
            raise ValueError('Unsafe storage path')
        db = sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)
        try:
            row = db.execute('SELECT account FROM jobs WHERE id=?', (args.job_id,)).fetchone()
            if not row:
                raise ValueError('Unknown download')
            rows = db.execute('SELECT m.id,m.original FROM messages m JOIN pending p ON p.message=m.id '
                              'WHERE p.job=? AND m.account=? AND p.state IN (\x27saved\x27,\x27reused\x27) '
                              'ORDER BY m.received DESC,m.id LIMIT ?', (args.job_id,row[0],args.limit)).fetchall()
        finally:
            db.close()
        records = [{'id': mid, 'original': json.loads(original)} for mid, original in rows]
        started = time.perf_counter()
        def progress(phase, completed, total):
            print(json.dumps({'phase': phase, 'encoded': completed, 'total': total}), flush=True)
        result = fit(records, ignore_attachments=args.ignore_attachments, progress=progress)
        result['seconds'] = round(time.perf_counter()-started, 2)
        output = private_path(str(directory / 'topic-runs'))
        output.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(output/(args.run_id+'.json'), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(result, stream)
        print(json.dumps({'processed': result['processed'], 'skipped': result['skipped'], 'seconds': result['seconds']}))
    except Exception:
        parser.exit(1, 'Local topic discovery failed. Check the installed dependencies, local model and stored sample.\n')


if __name__ == '__main__':
    main()
