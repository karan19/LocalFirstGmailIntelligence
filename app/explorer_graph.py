"""Bounded approximate neighbors and mode-weighted sparse community discovery."""
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
import numpy as np
from .explorer_index import CONFIG
from .explorer_views import attention,retention,terms,label as view_label,explanations as view_explanations

PUBLIC_DOMAINS={'gmail.com','yahoo.com','outlook.com','hotmail.com','icloud.com','proton.me','protonmail.com'}

def organization(record):
    if '_organization' in record:return record['_organization']
    domain=record['domain']
    if domain in PUBLIC_DOMAINS or not domain:return record['address']
    parts=domain.split('.')
    return '.'.join(parts[-3:] if len(parts)>2 and parts[-2] in ('co','com','org','ac') and len(parts[-1])==2 else parts[-2:])

def overlap(a,b):
    a,b=set(a),set(b)
    return len(a&b)/len(a|b) if a|b else 0

SUBJECT_STOP={'a','an','the','our','your','you','for','to','of','in','on','with','and','or','is','it','this','that','re','fw','fwd','don','miss','ends','soon','today','now','number','value','date','newsletter','update'}

def subject_terms(pattern):
    # Punctuation, apostrophes and sales urgency wording do not define a family.
    return set(re.findall(r'[a-z]{2,}',unicodedata.normalize('NFKC',pattern).casefold()))-SUBJECT_STOP

def score(a,b,semantic,mode):
    extra={}
    if mode=='purpose':extra={'terms':overlap(terms(a),terms(b))}
    elif mode=='attention':
        aa,ab=attention(a),attention(b)
        if aa=='unknown' or ab=='unknown' or aa!=ab:return 0
        extra={'attention':1.0}
    elif mode=='retention':
        ra,rb=retention(a),retention(b)
        if ra!=rb:return 0
        if ra=='unknown' and (not a.get('_prior_bucket') or a.get('_prior_bucket')!=b.get('_prior_bucket')):return 0
        extra={'retention':float(ra!='unknown' or bool(a.get('_prior_bucket'))),'prior':float(bool(a.get('_prior_bucket')) and a.get('_prior_bucket')==b.get('_prior_bucket')),'terms':overlap(terms(a),terms(b))}
    signals={**extra,'semantic':max(0,float(semantic)), 'sender':float(bool(a['address']) and a['address']==b['address']),
             'domain':float(bool(organization(a)) and organization(a)==organization(b)),
             'list':float(bool(a['list_id']) and a['list_id']==b['list_id']),
             'body':float(bool(a.get('body_template')) and a.get('body_template')==b.get('body_template')),
             'template':1.0 if a['pattern'] and a['pattern']==b['pattern'] else overlap(a['_subject_terms'] if '_subject_terms' in a else subject_terms(a['pattern']),b['_subject_terms'] if '_subject_terms' in b else subject_terms(b['pattern'])),
             'automation':float(a['automated'] and b['automated'])}
    if mode=='repetition' and not signals['sender'] and not signals['list'] and signals['template']<.8:
        return 0
    weights=CONFIG['modes'][mode]['weights']
    weighted=sum(signals.get(k,0)*weight for k,weight in weights.items())
    if mode=='repetition':
        family=CONFIG['modes'][mode]['semantic_family']
        if (signals['sender'] or signals['list']) and signals['automation'] and semantic>=family['minimum_cosine'] and signals['template']>=family['minimum_subject_overlap']:
            # Strong meaning plus a shared topic can establish repetition even
            # when a campaign rewrites its subject. Sender alone never suffices.
            weighted=max(weighted,CONFIG['modes'][mode]['threshold'])
    return weighted

CLUSTERERS={}
def register_clusterer(name,engine):CLUSTERERS[name]=engine

def louvain(graph,config):
    import networkx as nx
    if not graph.number_of_edges():return [{node} for node in graph]
    return nx.community.louvain_communities(graph,weight='weight',resolution=config['resolution'],seed=42)
register_clusterer('louvain',louvain)
register_clusterer('components',lambda graph,config: list(__import__('networkx').connected_components(graph)))

def build_clusters(records,vectors,mode):
    import hnswlib
    import networkx as nx
    records=[{**r,'_terms':terms(r),'_attention':attention(r),'_retention':retention(r),'_subject_terms':subject_terms(r['pattern']),'_organization':organization(r)} for r in records]
    config=CONFIG['modes'][mode];n=len(records);k=min(config['neighbors'],max(0,n-1))
    if not n:return [],[],{'nodes':0,'edges':0,'candidate_limit':0}
    matrix=np.asarray(vectors,dtype=np.float32)
    matrix=matrix/np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-9)
    ann=hnswlib.Index(space='cosine',dim=matrix.shape[1]);ann.init_index(max_elements=n,ef_construction=100,M=16,random_seed=42)
    ann.set_num_threads(1);ann.add_items(matrix,np.arange(n));ann.set_ef(max(50,k+1))
    labels,_=ann.knn_query(matrix,k=k+1,num_threads=1)
    candidates=[set(int(j) for j in row if int(j)!=i) for i,row in enumerate(labels)]
    # Bounded metadata rings prevent Source missing semantically dissimilar mail
    # from the same organization. No bucket creates all-pairs comparisons.
    groups=defaultdict(list)
    for i,r in enumerate(records):
        key=organization(r) if mode=='source' else (r['address'],r['pattern']) if mode=='repetition' else attention(r) if mode=='attention' else retention(r) if mode=='retention' else None
        if key is not None:groups[key].append(i)
    for members in groups.values():
        for pos,i in enumerate(members):
            for step in range(1,min(k//2+1,len(members))):
                candidates[i].add(members[(pos+step)%len(members)])
    graph=nx.Graph();graph.add_nodes_from(range(n))
    for i,neighbors in enumerate(candidates):
        ranked=sorted(((score(records[i],records[j],np.dot(matrix[i],matrix[j]),mode),j) for j in neighbors),reverse=True)[:k]
        for weight,j in ranked:
            if weight>=config['threshold']:graph.add_edge(i,j,weight=weight)
    communities=CLUSTERERS[config['algorithm']](graph,config)
    clusters=[];outliers=[]
    for community in communities:
        members=sorted(community,key=lambda i:records[i]['received'],reverse=True)
        if len(members)<config['min_cluster']:
            outliers.extend(records[i]['id'] for i in members);continue
        group=[records[i] for i in members];senders=Counter(r['address'] for r in group);domains=Counter(organization(r) for r in group);patterns=Counter(r['pattern'] for r in group)
        domain,domain_n=domains.most_common(1)[0];pattern,pattern_n=patterns.most_common(1)[0]
        auto=sum(r['automated'] for r in group)
        label=(domain or senders.most_common(1)[0][0]) if mode=='source' else (pattern or 'Recurring messages')
        if mode in ('purpose','attention','retention'):label=view_label(group,mode,pattern)
        explanation=view_explanations(group,mode) if mode in ('purpose','attention','retention') else [f'{round(100*domain_n/len(group))}% share source {domain or "unknown"}.',f'{round(100*pattern_n/len(group))}% share the most common normalized subject pattern.',f'{round(100*auto/len(group))}% have observed automated-mail signals.', 'Grouped using '+('source metadata and bounded similarity neighbors.' if mode=='source' else 'normalized subject wording, semantic similarity, sender/list identity and automated-mail signals; campaign wording may vary.')]
        cid=hashlib.sha256((config['version']+'|'+ '|'.join(sorted(r['id'] for r in group))).encode()).hexdigest()[:20]
        degree=dict(graph.subgraph(community).degree(weight='weight'))
        representative=sorted(members,key=lambda i:degree[i],reverse=True)[:5]
        clusters.append({'id':cid,'name':label[:160],'count':len(group),'percent':round(100*len(group)/n,1),
                         'first':min(r['received'] for r in group),'last':max(r['received'] for r in group),
                         'senders':senders.most_common(5),'domains':domains.most_common(5),'patterns':patterns.most_common(5),
                         'examples':[{key:value for key,value in records[i].items() if not key.startswith('_')} for i in representative], 'message_ids':[r['id'] for r in group],
                         'protected':sum(bool(r['protected']) for r in group),'attachments':sum(bool(r['attachments']) for r in group),
                         'retention_estimates':dict(Counter(retention(r) for r in group)) if mode=='retention' else None,
                         'prior_results':dict(Counter(r['_prior_bucket'] for r in group if r.get('_prior_bucket'))) if mode=='retention' else None,
                         'automated_percent':round(100*auto/len(group),1),
                         'explanation':explanation,
                         'cleanup_hint':'Large automated population to inspect' if auto/len(group)>.8 else 'Mixed or personal population; inspect before cleanup'})
    clusters.sort(key=lambda c:c['count'],reverse=True)
    return clusters,outliers,{'nodes':n,'edges':graph.number_of_edges(),'candidate_limit':k,'algorithm':config['algorithm'],'version':config['version']}
