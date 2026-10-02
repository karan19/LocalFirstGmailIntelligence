"""Explorer orchestration; graph discovery never authorizes mailbox writes."""
import asyncio
import copy
import datetime
import hashlib
import json
import os
import secrets
import time
from typing import Literal
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .services import UserError, normalize, Classifier
from .explorer_index import MailboxIndex, LocalEmbedder, CONFIG, MODEL, extract
from .explorer_graph import build_clusters

class ExploreOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['source','repetition','purpose','attention','retention']='source'
    scope:Literal['all','inbox','inbox_archive','sent']='all'
    limit:int=Field(default=20000,ge=1,le=CONFIG['max_messages'],strict=True)
    categories:list[Literal['primary','social','promotions','updates','forums']]=Field(default_factory=list)
    sender:str=Field(default='',max_length=300)
    after:datetime.date|None=None
    before:datetime.date|None=None
    @model_validator(mode='after')
    def dates(self):
        if self.scope=='sent' and self.categories:raise ValueError('Sent uses its own folder; remove Inbox category filters.')
        if self.after and self.before and self.after>=self.before:raise ValueError('End date must follow start date')
        return self
    def query(self):
        parts=[]
        if self.scope=='inbox':parts.append('in:inbox')
        elif self.scope=='sent':parts.append('in:sent')
        elif self.scope=='inbox_archive':parts.append('-in:spam -in:trash -in:sent -in:drafts')
        if self.categories:parts.append('{'+ ' '.join('category:'+c for c in sorted(set(self.categories)))+'}')
        if self.sender:
            # Sender filter is literal; arbitrary Gmail operators cannot broaden scope.
            if any(c in self.sender for c in ('"','\n','\r','{','}')):raise UserError('Use one sender address or domain.')
            parts.append('from:"'+self.sender+'"')
        if self.after:parts.append('after:'+self.after.isoformat())
        if self.before:parts.append('before:'+self.before.isoformat())
        return ' '.join(parts)

class ModeOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode:Literal['source','repetition','purpose','attention','retention']
class HandoffOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    classifier:Literal['laya','winnow']
    limit:int=Field(default=5000,ge=1,le=5000,strict=True)

class Explorer:
    def __init__(self,app,cleanup,subscriptions,index=None,embedder=None):
        self.cleanup,self.subscriptions=cleanup,subscriptions
        self.index=index or MailboxIndex();self.embedder=embedder or LocalEmbedder()
        self.task=None;self.switching=False;self.stop_requested=False;self.state={'status':'idle','listed':0,'indexed':0,'cached':0,'skipped':0,'stage':'idle','error':None}
        routes=[('GET','/api/explorer',self.current),('POST','/api/explorer/analyze',self.start),('POST','/api/explorer/stop',self.stop),
                ('GET','/api/explorer/reports/{rid}',self.report),('POST','/api/explorer/reports/{rid}/mode',self.switch_mode),
                ('GET','/api/explorer/reports/{rid}/clusters/{cid}',self.detail),('GET','/api/explorer/messages/{mid}',self.message),
                ('POST','/api/explorer/reports/{rid}/clusters/{cid}/review',self.handoff)]
        for method,path,handler in routes:app.add_api_route(path,handler,methods=[method])
    def busy(self):return self.switching or bool(self.task and not self.task.done())
    def idle(self):
        if self.busy():raise UserError('Explorer analysis is still running. Wait or stop it first.')
    async def account(self):
        async with self.subscriptions.connection() as (_,account):return account
    async def current(self):
        account=await self.account()
        if self.state.get('account_hash') != self.index.key(account):
            latest=self.index.latest_report(account)
            return {'supported_modes':list(CONFIG['modes']),'preview':os.environ.get('LOCAL_READONLY_PREVIEW')=='1','status':'done' if latest else 'idle','stage':'idle','listed':0,'indexed':0,'cached':0,'skipped':0,**({'report_id':latest['id']} if latest else {})}
        return {'supported_modes':list(CONFIG['modes']),'preview':os.environ.get('LOCAL_READONLY_PREVIEW')=='1',**{k:v for k,v in self.state.items() if k!='account_hash'}}
    async def start(self,payload:ExploreOptions):
        self.idle()
        # Do not compete with cleanup inference or overwrite a running cleanup session.
        if self.cleanup.busy() or self.cleanup.legacy_busy():raise UserError('Finish the current cleanup run before Explorer analysis.')
        payload.query()
        account=await self.account()
        self.idle()
        if self.cleanup.busy() or self.cleanup.legacy_busy():raise UserError('A cleanup run started; wait for it to finish.')
        rid=secrets.token_urlsafe(18)
        self.state={'id':rid,'status':'running','stage':'fetching','listed':0,'indexed':0,'cached':0,'skipped':0,'error':None,'mode':payload.mode,'account_hash':self.index.key(account)}
        self.stop_requested=False;self.task=asyncio.create_task(self.analyze(account,payload,rid))
        return {'id':rid}
    async def stop(self):
        account=await self.account()
        if self.state.get('account_hash') not in (None,self.index.key(account)):raise UserError('Analysis belongs to a different account.')
        if self.busy():self.stop_requested=True;self.state['status']='stopping'
        return {'stopping':self.stop_requested}
    async def analyze(self,account,options,rid):
        ids=[];seen=set();indexed=[]
        try:
            async with self.subscriptions.connection(account) as (gmail,_):
                token=None;tokens=set()
                while len(ids)<options.limit and not self.stop_requested:
                    params={'maxResults':min(500,options.limit-len(ids)),'includeSpamTrash':'true' if options.scope=='all' else 'false'}
                    if options.query():params['q']=options.query()
                    if token:params['pageToken']=token
                    page=await gmail.request('GET','messages',params)
                    for item in page.get('messages',[]):
                        if item['id'] not in seen and len(ids)<options.limit:ids.append(item['id']);seen.add(item['id'])
                    self.state['listed']=len(ids)
                    token=page.get('nextPageToken')
                    if not token:break
                    if token in tokens:raise UserError('Gmail pagination did not advance. Retry the analysis.')
                    tokens.add(token)
                # Fetch/derive/embed bounded batches; raw text never reaches SQLite.
                for start in range(0,len(ids),8):
                    if self.stop_requested:break
                    self.state['stage']='indexing'
                    raws=await asyncio.gather(*(gmail.message(mid) for mid in ids[start:start+8]),return_exceptions=True)
                    pending=[];texts=[]
                    for mid,raw in zip(ids[start:start+8],raws):
                        if isinstance(raw,BaseException):self.state['skipped']+=1;continue
                        try:
                            record,text,digest=extract(raw)
                            if record['id']!=mid:raise ValueError('Message identity changed')
                            existing=self.index.existing(account,mid,digest)
                            if existing is not None:
                                self.index.save(account,record,digest,existing);indexed.append(mid);self.state['cached']+=1;self.state['indexed']+=1
                            else:pending.append((record,digest));texts.append(text)
                        except (KeyError,ValueError):self.state['skipped']+=1
                    if texts:
                        self.state['stage']='embedding'
                        vectors=await asyncio.to_thread(self.embedder.embed,texts)
                        if len(vectors)!=len(pending):raise UserError('Embedding count mismatch. Retry the analysis.')
                        for (record,digest),vector in zip(pending,vectors):
                            self.index.save(account,record,digest,vector);indexed.append(record['id']);self.state['indexed']+=1
            self.state['stage']='clustering'
            report=await self.cluster(account,indexed,options.mode,rid,options.model_dump(mode='json'))
            report['partial']=self.stop_requested or bool(self.state['skipped']);report['requested']=len(ids);report['skipped']=self.state['skipped']
            self.index.save_report(account,report)
            self.state.update(status='stopped' if self.stop_requested else 'done',stage='done',report_id=rid)
        except UserError as exc:self.state.update(status='error',stage='error',error=str(exc))
        except Exception:self.state.update(status='error',stage='error',error='Explorer analysis failed. Check the local embedding runtime and disk space; existing index data is preserved.')
    def prior_results(self,account,ids):
        results=self.cleanup.intelligence.explorer_results(account,ids)
        if (self.cleanup.run.get('account') or '').casefold()==account.casefold():
            for mid in ids:
                message=self.cleanup.messages.get(mid,{})
                if message.get('bucket') in ('keep','ready','review'):results[mid]=message['bucket']
        return results
    async def cluster(self,account,ids,mode,rid,options,prior=None):
        indexed=self.index.records(account,ids)
        records=[r for r,_ in indexed];vectors=[v for _,v in indexed]
        if mode=='retention':
            prior=prior if prior is not None else self.prior_results(account,ids)
            for record in records:record['_prior_bucket']=prior.get(record['id'])
        clusters,outliers,graph=await asyncio.to_thread(build_clusters,records,vectors,mode)
        outlier_set=set(outliers)
        return {'id':rid,'corpus_id':rid,'mode':mode,'options':options,'count':len(records),'clusters':clusters,'outliers':outliers,
                'outlier_examples':[{k:v for k,v in r.items() if not k.startswith('_')} for r in records if r['id'] in outlier_set][:20], 'message_ids':ids,
                'graph':graph,'model':MODEL,'view_note':{'purpose':'Purpose groups are discovered from content similarity; labels summarize corpus language.','attention':'Observed interaction signals do not prove human importance or that you replied.','retention':'Retention estimates are provisional. Prior cleanup results are similarity features, not deletion authorization.'}.get(mode,''),'created':time.time(),'partial':False}
    def required(self,account,rid):
        report=self.index.report(account,rid)
        if not report:raise UserError('This Explorer report is unavailable for the connected account.')
        return report
    @staticmethod
    def public(report):
        return {**{k:v for k,v in report.items() if k not in ('message_ids','outliers')},'outlier_count':len(report['outliers']),
                'clusters':[{k:v for k,v in c.items() if k not in ('message_ids',)} for c in report['clusters']]}
    async def report(self,rid:str):return self.public(self.required(await self.account(),rid))
    async def detail(self,rid:str,cid:str):
        report=self.required(await self.account(),rid)
        cluster=next((c for c in report['clusters'] if c['id']==cid),None)
        if not cluster:raise UserError('Cluster not found in this report.')
        return {k:v for k,v in cluster.items() if k!='message_ids'}
    async def switch_mode(self,rid:str,payload:ModeOptions):
        self.idle()
        if self.cleanup.busy() or self.cleanup.legacy_busy():raise UserError('Finish the cleanup run before switching Explorer views.')
        self.switching=True
        try:
            account=await self.account();report=self.required(account,rid)
            corpus=report.get('corpus_id',report['id'])
            prior=self.prior_results(account,report['message_ids']) if payload.mode=='retention' else None
            fingerprint=json.dumps(prior,sort_keys=True) if prior is not None else ''
            new_id=hashlib.sha256((corpus+payload.mode+CONFIG['modes'][payload.mode]['version']+fingerprint).encode()).hexdigest()[:24]
            switched=self.index.report(account,new_id)
            if not switched:
                switched=await self.cluster(account,report['message_ids'],payload.mode,new_id,{**report['options'],'mode':payload.mode},prior=prior)
                switched.update(corpus_id=report.get('corpus_id',report['id']),partial=report.get('partial',False),requested=report.get('requested',report['count']),skipped=report.get('skipped',0))
                self.index.save_report(account,switched)
            self.state.update(report_id=new_id,mode=payload.mode,account_hash=self.index.key(account),status='done')
            return self.public(switched)
        finally:self.switching=False
    async def message(self,mid:str):
        account=await self.account()
        if not self.index.records(account,[mid]):raise UserError('Message is not in this account index.')
        async with self.subscriptions.connection(account) as (gmail,_):
            raw=await gmail.message(mid);normalized=normalize(raw)
            return {'id':mid,'sender':normalized['sender'],'subject':normalized['subject'],'received':normalized['received'],'text':normalized['text']}
    async def handoff(self,rid:str,cid:str,payload:HandoffOptions):
        self.idle()
        if self.cleanup.busy() or self.cleanup.legacy_busy():raise UserError('Finish the current cleanup run before reviewing a cluster.')
        account=await self.account();report=self.required(account,rid)
        cluster=next((c for c in report['clusters'] if c['id']==cid),None)
        if not cluster:raise UserError('Cluster not found.')
        # Check readiness before replacing an idle cleanup snapshot.
        if payload.classifier=='winnow' and not await asyncio.to_thread(self.cleanup.winnow.ready):raise UserError('Winnow is not ready; start it or choose Laya.')
        if payload.classifier=='laya' and isinstance(self.cleanup.classifier,Classifier):
            import importlib.util
            from readiness.cli import LAYA_PATH
            if importlib.util.find_spec('laya_mlx') is None or not (LAYA_PATH/'model.safetensors').is_file():raise UserError('Laya is not ready; install its local runtime and weights first.')
        self.idle()
        if self.cleanup.busy() or self.cleanup.legacy_busy():raise UserError('A cleanup run started; wait for it to finish.')
        self.cleanup.starting=True
        try:
            selected=cluster['message_ids'][:payload.limit];messages={};excluded=0
            async with self.subscriptions.connection(account) as (gmail,_):
                for start in range(0,len(selected),8):
                    raws=await asyncio.gather(*(gmail.message(mid) for mid in selected[start:start+8]),return_exceptions=True)
                    for mid,raw in zip(selected[start:start+8],raws):
                        if isinstance(raw,BaseException):excluded+=1;continue
                        if raw.get('id')!=mid or set(raw.get('labelIds',[]))&{'TRASH','SENT','DRAFT'}:excluded+=1;continue
                        m=normalize(raw);labels=set(raw.get('labelIds',[]));protected=sorted(labels & {'STARRED'})
                        m['list_id']=next((h['value'].strip().casefold() for h in raw.get('payload',{}).get('headers',[]) if h['name'].casefold()=='list-id'),'')
                        m.update(labels=sorted(labels),protected=protected,read_status='unread' if 'UNREAD' in labels else 'read',reviewed=False,bucket='keep' if protected else 'not_analyzed',source='protected' if protected else 'not_analyzed',trash_status=None)
                        messages[mid]=m
            if not messages:raise UserError('No eligible messages remain. Sent, Drafts, Trash and unavailable messages are excluded.')
            self.cleanup.reset();self.cleanup.messages=messages;self.cleanup.fetched_snapshot=copy.deepcopy(messages)
            run_id=secrets.token_urlsafe(18)
            self.cleanup.run.update(id=run_id,status='done',stage='done',account=account,listed=len(selected),fetched=len(messages),limit=payload.limit,
                                    explorer_handoff={'report':rid,'cluster':cid,'cluster_count':cluster['count'],'reviewed':len(messages),'excluded':excluded,'remaining':max(0,cluster['count']-len(selected))})
        finally:self.cleanup.starting=False
        await self.cleanup.start_branch(run_id,payload.classifier)
        return {'run_id':run_id,**self.cleanup.run['explorer_handoff']}
