"""Inspectable Explorer features, not mailbox-action decisions."""
import re
from collections import Counter

ATTENTION_NAMES={'list':'Mailing-list activity','automated':'Automated activity','conversation':'Reply-linked conversations','direct':'Direct mail signals','unknown':'Unclear interaction'}
RETENTION_NAMES={'transient':'Likely transient','record':'Possible durable records','action':'Action-related — status unverified','unknown':'Unclear retention'}

def attention(record):
    if '_attention' in record:return record['_attention']
    if record.get('list_id'):return 'list'
    if record.get('automated'):return 'automated'
    if record.get('reply_header'):return 'conversation'
    if record.get('recipients') and record.get('address'):return 'direct'
    return 'unknown'

def retention(record):
    if '_retention' in record:return record['_retention']
    subject=record['subject'].casefold()
    if re.search(r'\b(one[- ]time|verification code|verify code|otp|security code|authentication code)\b',subject):return 'transient'
    durable=bool(re.search(r'\b(statement|tax document|tax record|lease|contract|medical record|insurance policy|employment agreement|immigration)\b',subject))
    action=bool(re.search(r'\b(overdue|payment due|action required|dispute|appointment|court|legal notice)\b',subject))
    transient=bool(re.search(r'\b(receipt|newsletter|digest|sale|promotion|offer|delivery|ride|build|workflow|shipping)\b',subject)) or bool(record.get('list_id'))
    if action:return 'action'
    if durable:return 'record'
    if transient:return 'transient'
    return 'unknown'

def terms(record):
    if '_terms' in record:return record['_terms']
    return set(record.get('terms',[]))|set(re.findall(r'[a-z]{3,}',record['subject'].casefold()))

def label(group,mode,pattern):
    if mode=='attention':return ATTENTION_NAMES[Counter(attention(r) for r in group).most_common(1)[0][0]]
    if mode=='retention':
        cue=Counter(retention(r) for r in group).most_common(1)[0][0]
        prior=Counter(r.get('_prior_bucket') for r in group if r.get('_prior_bucket'))
        if cue=='unknown' and prior:return 'Prior '+{'keep':'Keep','ready':'Cleanup','review':'Needs Review'}[prior.most_common(1)[0][0]]+' signals'
        return RETENTION_NAMES[cue]
    # Purpose labels describe discovered corpus language, never a forced taxonomy.
    stop={'your','this','that','with','from','have','will','email','https','http','click','here','please','today','soon','unsubscribe','their','there','link','number','value'}
    common=Counter(word for r in group for word in terms(r)-stop)
    anchors=[word for word,n in common.most_common(3) if n>=max(2,len(group)*.4)]
    return 'Shared purpose · '+(' / '.join(anchors) if anchors else pattern[:100] or 'similar content')

def explanations(group,mode):
    n=len(group)
    if mode=='purpose':return ['Discovered from local semantic similarity and shared content terms across the selected corpus.','The label summarizes frequent language; it is not a predefined purpose category or a cleanup recommendation.']
    if mode=='attention':
        counts=Counter(attention(r) for r in group)
        return [f'{round(100*count/n)}% have {ATTENTION_NAMES[key].lower()}.' for key,count in counts.most_common()]+['These are observed interaction signals. A reply header does not prove you replied or that a message is personally meaningful.']
    cues=Counter(retention(r) for r in group);prior=Counter(r.get('_prior_bucket') for r in group if r.get('_prior_bucket'))
    return [f'{round(100*count/n)}% have {RETENTION_NAMES[key].lower()} cues.' for key,count in cues.most_common()]+[f'{count} messages have a prior {bucket} cleanup result.' for bucket,count in prior.items()]+['Retention estimates use subject/list cues and available prior results. Current action status and durable personal value are not verified; use cleanup review before any action.']
