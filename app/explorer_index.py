"""Private account-separated derived mailbox index; bodies/HTML are never stored."""
import hashlib
import json
import re
from collections import Counter
from email.utils import parseaddr, getaddresses
import numpy as np
from .history import ReviewHistory
from .services import ROOT, decision_text, normalize

CONFIG = json.loads((ROOT/'app/explorer_config.json').read_text())
MODEL = CONFIG['embedding_model']

def subject_pattern(subject):
    value = re.sub(r'^(?:(?:re|fw|fwd):\s*)+', '', subject.casefold()).strip()
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+', '<value>', value)
    value = re.sub(r'\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b', '<date>', value)
    return re.sub(r'\d+(?:[.,:/-]\d+)*', '<number>', value)

def extract(raw):
    message = normalize(raw)
    headers = {h['name'].casefold(): h['value'] for h in raw.get('payload',{}).get('headers',[])}
    address = parseaddr(message['sender'])[1].casefold()
    domain = address.rsplit('@',1)[-1] if '@' in address else ''
    words = re.findall(r'\b[a-z][a-z-]{3,}\b', decision_text(message['text'], 3000).casefold())
    stop = {'this','that','with','from','your','have','will','http','https','link','unsubscribe','email','please','click','here','there','they','what','when','which','about'}
    terms = [word for word,_ in Counter(w for w in words if w not in stop).most_common(24)]
    automated = any(headers.get(h) for h in ('list-id','list-unsubscribe','auto-submitted','precedence')) or bool(re.search(r'no.?reply|noreply|notifications?',address))
    def attachments(payload):
        result=[]
        if payload.get('filename'):
            result.append({'filename':payload['filename'][:300],'mime':payload.get('mimeType'),'size':payload.get('body',{}).get('size',0)})
        for part in payload.get('parts',[]):result.extend(attachments(part))
        return result
    body_template=hashlib.sha256(subject_pattern(decision_text(message['text'],3000)).encode()).hexdigest() if message['text'].strip() else ''
    authentication_domains=sorted(set(re.findall(r'(?:header\.d|smtp\.mailfrom)=([a-zA-Z0-9.-]+)',headers.get('authentication-results',''))))[:10]
    record = {'body_template':body_template,'authentication_domains':authentication_domains,'id':message['id'],'thread_id':raw.get('threadId',''), 'sender':message['sender'][:500], 'address':address[:300], 'domain':domain[:300],
              'reply_to':parseaddr(headers.get('reply-to',''))[1][:300], 'list_id':headers.get('list-id','')[:300],
              'recipients':[a for _,a in getaddresses([headers.get('to',''),headers.get('cc','')])][:50],
              'received':message['received'], 'subject':message['subject'][:500], 'pattern':subject_pattern(message['subject'])[:500],
              'terms':terms,'automated':bool(automated),'reply_header':bool(headers.get('in-reply-to')), 'labels':raw.get('labelIds',[]),
              'protected':sorted(set(raw.get('labelIds',[])) & {'STARRED'}), 'attachments':attachments(raw.get('payload',{})), 'feature_version':'features_v1'}
    # Only in-memory inference input; never pass it to persistence.
    text = decision_text(message['subject']+'\n'+message['text'],2000)
    digest=hashlib.sha256((MODEL+'\n'+CONFIG['embedding_revision']+'\nfeatures_v1\n'+text).encode()).hexdigest()
    return record,text,digest

class MailboxIndex(ReviewHistory):
    def __init__(self,path=None):super().__init__(path or ROOT/'.local/explorer/index.sqlite3')
    @staticmethod
    def key(account):return hashlib.sha256(account.strip().casefold().encode()).hexdigest()
    def initialize(self,db):
        db.execute('CREATE TABLE IF NOT EXISTS explorer_messages(account TEXT,id TEXT,data TEXT,digest TEXT,embedding BLOB,PRIMARY KEY(account,id))')
        db.execute('CREATE TABLE IF NOT EXISTS explorer_reports(account TEXT,id TEXT,data TEXT,PRIMARY KEY(account,id))')
    def existing(self,account,mid,digest):
        with self.database() as db:
            self.initialize(db);row=db.execute('SELECT embedding FROM explorer_messages WHERE account=? AND id=? AND digest=?',(self.key(account),mid,digest)).fetchone()
        return np.frombuffer(row[0],dtype=np.float32).copy() if row else None
    def save(self,account,record,digest,embedding):
        # Allowlist prevents accidental raw-body/HTML persistence.
        allowed={'body_template','authentication_domains','id','thread_id','sender','address','domain','reply_to','list_id','recipients','received','subject','pattern','terms','automated','reply_header','labels','protected','attachments','feature_version'}
        if set(record)-allowed:raise ValueError('Unexpected index field')
        with self.database() as db:
            self.initialize(db);db.execute('INSERT OR REPLACE INTO explorer_messages VALUES(?,?,?,?,?)',(self.key(account),record['id'],json.dumps(record),digest,np.asarray(embedding,dtype=np.float32).tobytes()))
    def records(self,account,ids):
        rows=[]
        with self.database() as db:
            self.initialize(db)
            for i in range(0,len(ids),500):
                chunk=ids[i:i+500]
                if not chunk:continue
                rows.extend(db.execute('SELECT data,embedding FROM explorer_messages WHERE account=? AND id IN ('+','.join('?' for _ in chunk)+')',[self.key(account),*chunk]).fetchall())
        by_id={json.loads(data)['id']:(json.loads(data),np.frombuffer(vector,dtype=np.float32).copy()) for data,vector in rows}
        return [by_id[mid] for mid in ids if mid in by_id]
    def save_report(self,account,report):
        with self.database() as db:
            self.initialize(db);db.execute('INSERT OR REPLACE INTO explorer_reports VALUES(?,?,?)',(self.key(account),report['id'],json.dumps(report)))
    def latest_report(self,account):
        with self.database() as db:
            self.initialize(db);row=db.execute('SELECT data FROM explorer_reports WHERE account=? ORDER BY rowid DESC LIMIT 1',(self.key(account),)).fetchone()
        return json.loads(row[0]) if row else None
    def report(self,account,rid):
        with self.database() as db:
            self.initialize(db);row=db.execute('SELECT data FROM explorer_reports WHERE account=? AND id=?',(self.key(account),rid)).fetchone()
        return json.loads(row[0]) if row else None

class LocalEmbedder:
    def __init__(self):self.model=None
    def embed(self,texts):
        if self.model is None:
            from fastembed import TextEmbedding
            self.model=TextEmbedding(model_name=MODEL,cache_dir=str(ROOT/'.local/explorer/models'),threads=1)
        return [np.asarray(v,dtype=np.float32) for v in self.model.embed(texts,batch_size=16)]
