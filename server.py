import asyncio
import base64
import binascii
import hashlib
import hmac
import re
import contextlib
import json
import os
import secrets
import time
import uuid
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout, WSMsgType
from quiz import Quiz, PROMPT
from settings import load, transport, openai_endpoints
from audit import configure, emit, failure, api_response, error_fields, clean, safe_url, response_headers

ROOT=Path(__file__).resolve().parent
def eid(): return 'evt_'+uuid.uuid4().hex

class Call:
    def __init__(self, sid, mode, app, quiz=None):
        self.sid=sid; self.mode=mode; self.app=app
        self.quiz=quiz if quiz is not None else Quiz(); self.ws=None; self.task=None; self.jobs=set()
        self.lock=asyncio.Lock(); self.close_lock=asyncio.Lock()
        self.seen=set(); self.delegations={}; self.processed=set(); self.pending={}; self.decision_offset=-1; self.ready_for_decision=True
        self.status='dialing' if mode=='sip' else 'connected'
        self.error=''; self.usage=None; self.created=time.monotonic(); self.finished_at=None
        self.ended=False
        emit('call.created',session_id=sid,mode=mode)
    def state(self):
        return dict(self.quiz.snapshot(), connection=self.status, mode=self.mode, error=self.error, usage=self.usage)
    async def send(self, typ, **fields):
        if not self.ws or self.ws.closed: raise ConnectionError('Sideband closed')
        emit('sideband.send','DEBUG',session_id=self.sid,event_type=typ)
        await self.ws.send_json(dict(type=typ,event_id=eid(),**fields))
    async def confirmed(self,typ,ack,**fields):
        ident=eid()
        future=asyncio.get_running_loop().create_future()
        self.pending[ident]=(ack,future)
        try:
            await self.ws.send_json(dict(type=typ,event_id=ident,**fields))
            emit('sideband.command',session_id=self.sid,event_type=typ,client_event_id=ident)
            return await asyncio.wait_for(future,10)
        finally:
            self.pending.pop(ident,None)
    async def handle(self,e):
        eventid=e.get('event_id'); typ=e.get('type')
        if eventid and eventid in self.seen: return
        if eventid: self.seen.add(eventid)
        if typ in ('session.input_transcript.delta','session.output_transcript.delta','session.input_audio.delta','session.output_audio.delta'):
            return
        emit('sideband.event','DEBUG',session_id=self.sid,event_type=clean(typ,self.app['cfg']))
        pending=self.pending.get(e.get('client_event_id') or (e.get('error') or {}).get('client_event_id'))
        if pending and not pending[1].done():
            if typ==pending[0]: pending[1].set_result(e)
            elif typ=='error': pending[1].set_exception(RuntimeError('Live command rejected'))
        previous=self.status
        if typ=='transport.ringing' and self.status=='dialing': self.status='ringing'
        elif typ=='transport.answered' and not self.ended: self.status='connected'
        elif typ=='transport.failed':
            self.status='failed'; self.error='SIP transport failed'; self.ended=True
        elif typ=='session.delegation.created' and e.get('delegation',{}).get('target')=='decisions':
            d=e['delegation']['id']; offset=e.get('offset_ms',-1)
            if d not in self.delegations:
                self.delegations[d]=(self.quiz.decision_name,offset,self.ready_for_decision)
                emit('delegation.received',session_id=self.sid,delegation_id=d,stage=self.quiz.stage)
        elif typ=='decision.event' and e.get('delegation_id') and e.get('event',{}).get('type')=='decision.completed':
            d=e['delegation_id']
            if d not in self.processed:
                self.processed.add(d)
                job=asyncio.create_task(self.decide(d,e['event']['decision']))
                self.jobs.add(job); job.add_done_callback(self.jobs.discard)
        elif typ=='session.closed':
            self.status='closed'; self.usage=e.get('usage'); self.ended=True
        elif typ=='error':
            self.error='Live API event error'
            emit('sideband.api_error','ERROR',session_id=self.sid,**error_fields(e,self.app['cfg']))
        if typ=='transport.failed':
            emit('sip.transport_failed','ERROR',session_id=self.sid,**error_fields(e,self.app['cfg']))
        if previous!=self.status:
            emit('call.transition',session_id=self.sid,mode=self.mode,previous=previous,status=self.status)
    async def decide(self,d,decision):
        async with self.lock:
            try:
                if self.ended or self.quiz.stage=='finished': return
                meta=self.delegations.get(d)
                name=self.quiz.decision_name
                if not meta or meta[0]!=name or not meta[2] or meta[1]<=self.decision_offset:
                    emit('decision.stale','WARNING',session_id=self.sid,delegation_id=d,expected=name)
                    return
                self.ready_for_decision=False
                answers=[a for a in decision.get('answers',[]) if a.get('name')==name]
                a=answers[0] if len(answers)==1 else {}
                allowed={x['value'] for x in self.quiz.classifier()['choices']}
                choice=a.get('choice'); confidence=a.get('confidence')
                valid=a.get('type')=='choice' and isinstance(choice,str) and choice in allowed and type(confidence) in (float,int) and 0<=confidence<=1
                accepted=choice if valid and confidence>=self.app['cfg'].get('decision_min_confidence',.8) else 'unclear'
                self.quiz.last_decision=dict(choice=accepted,confidence=confidence if valid else None)
                emit('decision.classified',session_id=self.sid,delegation_id=d,stage=self.quiz.stage,classification=accepted,confidence=confidence if valid else None)
                message=self.quiz.advance(accepted)
                if self.quiz.stage!='finished':
                    await self.confirmed('session.update','session.updated',session={'delegation':self.quiz.delegation(self.app['cfg'].get('decision_model','gpt-6-luna'))})
                ack=await self.confirmed('session.commentary.append','session.commentary.appended',delegation_id=d,content=message)
                self.decision_offset=max(meta[1],ack.get('end_ms',meta[1]))
                self.ready_for_decision=True
                if self.quiz.stage=='finished': self.finished_at=time.monotonic()
            except Exception as exc:
                self.error='Decision processing: '+type(exc).__name__
                failure('decision.failed',exc,session_id=self.sid)
                # No further scoring with unconfirmed configuration.
                self.quiz.finish('technical_error','')
                self.finished_at=time.monotonic()
                with contextlib.suppress(Exception):
                    await self.send('session.commentary.append',delegation_id=None,content='Omluv se za technický problém a rozluč se.')
    async def reader(self):
        emit('sideband.connected',session_id=self.sid,mode=self.mode)
        try:
            async for msg in self.ws:
                if msg.type==WSMsgType.TEXT: await self.handle(json.loads(msg.data))
                elif msg.type==WSMsgType.ERROR:
                    emit('sideband.socket_error','ERROR',session_id=self.sid,exception=type(self.ws.exception()).__name__)
                    break
        except Exception as exc:
            self.error='Sideband: '+type(exc).__name__
            failure('sideband.reader_failed',exc,session_id=self.sid)
        finally:
            if not self.ended and self.status!='closing': self.status='disconnected'
            emit('sideband.closed',session_id=self.sid,status=self.status,close_code=self.ws.close_code)
    async def close(self):
        async with self.close_lock:
            if not self.ended:
                self.status='closing'
                emit('call.closing',session_id=self.sid,mode=self.mode)
                try:
                    if self.mode in ('sip','sip_inbound'):
                        async with self.app['http'].post(f"{self.app['api_url']}/{self.sid}/hangup",headers=self.app['auth'],allow_redirects=False,timeout=ClientTimeout(total=10)) as r:
                            await api_response('hangup',r,self.app['cfg'],session_id=self.sid)
                            if r.status not in (200,404): raise RuntimeError(f'Hangup HTTP {r.status}')
                    else: await self.send('session.close')
                except Exception as exc:
                    failure('call.hangup_failed',exc,session_id=self.sid)
                    self.status='close_failed'; self.error='Termination not confirmed; retry stop'
                    return
                self.ended=True
            if self.task and not self.task.done():
                with contextlib.suppress(asyncio.TimeoutError,Exception):
                    await asyncio.wait_for(asyncio.shield(self.task),4)
            if self.ws: await self.ws.close()
            for j in list(self.jobs): j.cancel()
            if self.jobs: await asyncio.gather(*self.jobs,return_exceptions=True)
            if self.task and not self.task.done():
                self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
            if self.status not in ('closed','failed'): self.status='closed'
            emit('call.ended',session_id=self.sid,status=self.status,duration_seconds=round(time.monotonic()-self.created,2))

@web.middleware
async def guard(req, handler):
    if req.path not in ('/','/healthz','/webhooks/openai'):
        expected=req.app['cfg']['operator_token']
        supplied=req.headers.get('Authorization','')
        if not secrets.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
            emit('operator.auth_failed','WARNING',method=req.method)
            raise web.HTTPUnauthorized(text='Operator token required')
        if req.method=='POST' and req.headers.get('Origin')!=req.app['cfg']['public_origin']:
            emit('operator.origin_rejected','WARNING',method=req.method)
            raise web.HTTPForbidden(text='Invalid origin')
    response=await handler(req)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

async def index(req): return web.FileResponse(ROOT/'index.html')
async def health(req): return web.json_response({'ok':True,'version':os.getenv('APP_VERSION','local')})
async def config(req):
    cfg=req.app['cfg']
    return web.json_response(dict(model=cfg['model'],sip_enabled=bool(cfg.get('sip')),sip_inbound_enabled=False,max_call_seconds=cfg['max_call_seconds']))
def session_payload(cfg, t, quiz=None):
    greeting='Po spojení se představ jako AI moderátor, nabídni soutěž a zeptej se na souhlas.'
    quiz=quiz if quiz is not None else Quiz()
    session=dict(model=cfg['model'],instructions=PROMPT,audio={'output':{'voice':cfg['voice']}},
        delegation=quiz.delegation(cfg.get('decision_model','gpt-6-luna')),
        input=[{'type':'message','role':'developer','content':[{'type':'input_text','text':greeting}]}])
    if t['type']=='webrtc':
        session['client']={'data_channel':{'allowed_client_events':[], 'allowed_server_events':[{'type':'session.started'},{'type':'session.closed'},{'type':'error'}]}}
    return {'session':session,'transport':t}

def inbound_payload(cfg):
    session=session_payload(cfg,{'type':'sip'})['session']
    session['type']='live'
    session['instructions'] += ' Volající zavolal sám. Neříkej, že jsi mu telefonoval. Pozdrav jej jako příchozího účastníka fiktivní soutěže.'
    session['input'][0]['content'][0]['text']='Zahaj: Dobrý den, dovolali jste se automatizovanému moderátorovi fiktivní soutěže. Chcete se zúčastnit?'
    return {'session':session}

def verify_webhook(body, headers, secret, now=None):
    """Standard Webhooks v1 HMAC over the original request bytes."""
    try:
        ident=headers['webhook-id']
        stamp=headers['webhook-timestamp']
        timestamp=int(stamp)
        if not ident or abs((time.time() if now is None else now)-timestamp)>300:
            return False
        key=base64.b64decode(secret.removeprefix('whsec_'),validate=True)
        if not key: return False
        expected=hmac.digest(key,ident.encode()+b'.'+stamp.encode()+b'.'+body,'sha256')
        for signature in headers['webhook-signature'].split():
            version,sep,value=signature.partition(',')
            if sep and version=='v1':
                try:
                    if hmac.compare_digest(expected,base64.b64decode(value,validate=True)):
                        return True
                except binascii.Error:
                    continue
    except (KeyError,ValueError,TypeError,binascii.Error):
        pass
    return False

async def probe_models(app):
    cfg=app['cfg']
    url=app['api_url'].removesuffix('/live/sessions')+'/models'
    started=time.monotonic()
    emit('openai.probe.request',method='GET',url=url)
    try:
        async with app['http'].get(url,headers=app['auth'],allow_redirects=False,
                timeout=ClientTimeout(total=10)) as r:
            await api_response('models_probe',r,cfg,elapsed_ms=round((time.monotonic()-started)*1000))
            if r.status==200:
                data=await r.json()
                models=data.get('data',[])
                emit('openai.probe.result',model_count=len(models),configured_model_present=any(
                    isinstance(m,dict) and m.get('id')==cfg['model'] for m in models))
    except Exception as exc:
        failure('openai.probe.failed',exc,url=url)

async def inbound_worker(app,sid):
    """Serialize acceptance with browser/outbound calls; do not trust caller headers."""
    async with app['start_lock']:
        if any(not c.ended for c in app['calls'].values()):
            emit('inbound.busy',session_id=sid)
            async with app['http'].post(f"{app['api_url']}/{sid}/reject",headers=app['auth'],allow_redirects=False,
                    json={'status_code':486},timeout=ClientTimeout(total=10)) as r:
                await api_response('reject',r,app['cfg'],session_id=sid)
                if r.status!=200: raise RuntimeError(f'Reject HTTP {r.status}')
            return
        app['calls'].clear()
        # Reserve the slot before issuing the accept request.
        call=Call(sid,'sip_inbound',app)
        call.status='accepting'
        token=secrets.token_urlsafe(24)
        app['calls'][token]=call
        # The operator may see the reservation but cannot stop it during setup:
        # stop() takes the same start_lock.
        try:
            url=f"{app['api_url']}/{sid}/accept"
            started=time.monotonic()
            emit('openai.http.request',operation='accept',method='POST',url=url,session_id=sid,model=app['cfg']['model'],payload_keys=sorted(inbound_payload(app['cfg'])['session']))
            async with app['http'].post(url,headers=app['auth'],allow_redirects=False,
                    json=inbound_payload(app['cfg']),timeout=ClientTimeout(total=12)) as r:
                details=await api_response('accept',r,app['cfg'],session_id=sid,elapsed_ms=round((time.monotonic()-started)*1000))
                if r.status!=200:
                    call.status='failed'; call.error=f'Accept HTTP {r.status}'
                    if details.get('request_id'): call.error+=' / request_id='+details['request_id']
                    call.ended=True
                    return
            call.status='connected'
            emit('inbound.accepted',session_id=sid)
            emit('sideband.attach',session_id=sid)
            call.ws=await app['http'].ws_connect(f"{app['ws_url']}/{sid}/attach",headers=app['auth'],
                    heartbeat=20,max_msg_size=4*1024*1024)
            call.task=asyncio.create_task(call.reader())
        except Exception as exc:
            call.error='Inbound setup: '+type(exc).__name__
            failure('inbound.setup_failed',exc,session_id=sid,http_status=getattr(exc,'status',None))
            # Acceptance may have succeeded even if its response was lost.
            await call.close()
            if call.ended:
                call.status='failed'

async def webhook(req):
    cfg=req.app['cfg'].get('sip_inbound',{})
    if True:  # Temporarily disabled even if legacy configuration enables it.
        emit('webhook.disabled','WARNING')
        raise web.HTTPNotFound()
    body=await req.read()
    if not verify_webhook(body,req.headers,cfg['webhook_secret']):
        emit('webhook.signature_invalid','WARNING',body_bytes=len(body),header_id_present=bool(req.headers.get('webhook-id')),header_signature_present=bool(req.headers.get('webhook-signature')),header_timestamp_present=bool(req.headers.get('webhook-timestamp')),timestamp_age_seconds=round(time.time()-int(req.headers['webhook-timestamp'])) if re.fullmatch(r'[0-9]{1,12}',req.headers.get('webhook-timestamp','')) else None)
        raise web.HTTPUnauthorized(text='Invalid webhook signature')
    try:
        event=json.loads(body)
        if not isinstance(event,dict): raise ValueError()
    except (ValueError,UnicodeError):
        emit('webhook.invalid_json','WARNING')
        raise web.HTTPBadRequest(text='Invalid event')
    if event.get('type') not in ('live.transport.incoming','live.call.incoming'):
        return web.Response(status=204)
    data=event.get('data')
    if not isinstance(data,dict): raise web.HTTPBadRequest(text='Missing event data')
    if event['type']=='live.transport.incoming' and data.get('type')!='sip':
        return web.Response(status=204)
    sid=data.get('session_id')
    if not isinstance(sid,str) or not re.fullmatch(r'live_[A-Za-z0-9_-]{1,200}',sid):
        raise web.HTTPBadRequest(text='Invalid session ID')
    # Deduplicate both webhook delivery and pending session, even across legacy event names.
    seen=req.app['inbound_seen']; now=time.monotonic()
    for key,expiry in list(seen.items()):
        if expiry<=now: del seen[key]
    keys=('delivery:'+req.headers['webhook-id'],'session:'+sid)
    if any(key in seen for key in keys):
        emit('webhook.duplicate',session_id=sid)
        return web.Response(status=204)
    if len(seen)>=20000 or len(req.app['inbound_jobs'])>=16:
        emit('webhook.capacity','WARNING',session_id=sid)
        raise web.HTTPServiceUnavailable(text='Inbound capacity reached')
    for key in keys: seen[key]=now+73*3600
    emit('webhook.accepted',session_id=sid,event_type=event['type'],delivery_id=clean(req.headers['webhook-id'],req.app['cfg']))
    async def work():
        try:
            await inbound_worker(req.app,sid)
        except Exception as exc:
            # No credentials or caller data in logs.
            failure('inbound.decision_failed',exc,session_id=sid)
            # Allow webhook retries only if no slot was accepted/reserved for this session.
            if not any(c.sid==sid for c in req.app['calls'].values()):
                for key in keys: seen.pop(key,None)
    job=asyncio.create_task(work())
    req.app['inbound_jobs'].add(job)
    job.add_done_callback(req.app['inbound_jobs'].discard)
    return web.Response(status=204)
async def start(req):
    try:
        data=await req.json()
        if not isinstance(data,dict): raise ValueError('JSON object required')
        t=transport(data,req.app['cfg'])
    except (ValueError,TypeError) as exc:
        emit('session.invalid_request','WARNING',reason=clean(str(exc),req.app['cfg']))
        raise web.HTTPBadRequest(text=str(exc))
    async with req.app['start_lock']:
        if any(not c.ended for c in req.app['calls'].values()):
            emit('session.busy','WARNING',mode=t['type'])
            raise web.HTTPConflict(text='An active call already exists; stop it first')
        req.app['calls'].clear()
        http=req.app['http']; cfg=req.app['cfg']; call=None; quiz=Quiz()
        try:
            emit('openai.http.request',operation='create_session',api_url=req.app['api_url'],mode=t['type'],model=cfg['model'])
            async with http.post(req.app['api_url'],headers=req.app['auth'],allow_redirects=False,json=session_payload(cfg,t,quiz)) as r:
                await api_response('create_session',r,cfg,mode=t['type'])
                if r.status>=300: raise web.HTTPBadGateway(text=f'OpenAI HTTP {r.status}')
                body=await r.json()
            sid=body['session']['id']; call=Call(sid,t['type'],req.app,quiz)
            token=secrets.token_urlsafe(24); req.app['calls'][token]=call
            emit('sideband.attach',session_id=sid)
            call.ws=await http.ws_connect(f"{req.app['ws_url']}/{sid}/attach",headers=req.app['auth'],heartbeat=20,max_msg_size=4*1024*1024)
            call.task=asyncio.create_task(call.reader())
            result=dict(token=token,session_id=sid,mode=t['type'])
            if t['type']=='webrtc': result['sdp']=body['transport']['sdp']
            return web.json_response(result)
        except web.HTTPException: raise
        except Exception as exc:
            failure('session.setup_failed',exc,session_id=call.sid if call else None,http_status=getattr(exc,'status',None))
            if call: await call.close()
            raise web.HTTPBadGateway(text='Session setup failed: '+type(exc).__name__)
def get_call(req):
    c=req.app['calls'].get(req.match_info['token'])
    if not c: raise web.HTTPNotFound(text='Session not found')
    return c
async def status(req): return web.json_response(get_call(req).state())
async def stop(req):
    async with req.app['start_lock']:
        c=get_call(req); emit('operator.hangup',session_id=c.sid); await c.close(); return web.json_response(c.state())
async def active(req):
    return web.json_response({'calls':[dict(token=t,**c.state()) for t,c in req.app['calls'].items()]})
async def lifecycle(app):
    cfg=app['cfg']
    emit('service.started',openai_region=cfg.get('openai_region','global'),api_url=app['api_url'],ws_url=app['ws_url'],version=os.getenv('APP_VERSION','local'),model=cfg['model'],sip_outbound=bool(cfg.get('sip')),sip_inbound=False,decision_model=cfg.get('decision_model','gpt-6-luna'),log_level=cfg.get('log_level','INFO'),max_call_seconds=cfg['max_call_seconds'])
    app['http']=ClientSession(timeout=ClientTimeout(total=35))
    app['calls']={}; app['start_lock']=asyncio.Lock()
    app['inbound_seen']={}; app['inbound_jobs']=set()
    async def reap():
        while True:
            await asyncio.sleep(2)
            for c in list(app['calls'].values()):
                now=time.monotonic()
                # Allow time for the spoken farewell; not a playback-complete signal.
                if c.status=='accepting': continue
                if not c.ended and (now-c.created>app['cfg']['max_call_seconds'] or
                    (c.finished_at is not None and now-c.finished_at>15) or
                    c.status in ('disconnected','close_failed')):
                    emit('call.auto_hangup',session_id=c.sid,status=c.status)
                    await c.close()
    task=asyncio.create_task(reap())
    probe=asyncio.create_task(probe_models(app)) if cfg.get('diagnostic_models_probe',False) else None
    yield
    if probe:
        probe.cancel(); await asyncio.gather(probe,return_exceptions=True)
    task.cancel(); await asyncio.gather(task,return_exceptions=True)
    if app['inbound_jobs']:
        await asyncio.gather(*list(app['inbound_jobs']),return_exceptions=True)
    for c in app['calls'].values(): await c.close()
    await app['http'].close()
    emit('service.stopped')

def create_app(cfg):
    app=web.Application(middlewares=[guard],client_max_size=150000)
    app['api_url'], app['ws_url']=openai_endpoints(cfg.get('openai_region','global'))
    app['cfg']=cfg; app['auth']={'Authorization':'Bearer '+cfg['openai_api_key']}
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/',index),web.get('/healthz',health),web.get('/config',config),
        web.post('/webhooks/openai',webhook),web.post('/session',start),web.get('/active',active),web.get('/state/{token}',status),web.post('/stop/{token}',stop)])
    return app
if __name__=='__main__':
    configure()
    try:
        cfg=load()
        configure(cfg['log_level'])
    except Exception as exc:
        failure('service.configuration_failed',exc)
        raise SystemExit(1) from None
    web.run_app(create_app(cfg),host=os.getenv('BIND_HOST','127.0.0.1'),port=int(os.getenv('PORT','8765')),access_log=None)
