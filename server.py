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
from settings import load, transport

ROOT=Path(__file__).resolve().parent
API='https://api.openai.com/v1/live/sessions'
WS='wss://api.openai.com/v1/live/sessions'
def eid(): return 'evt_'+uuid.uuid4().hex

class Call:
    def __init__(self, sid, mode, app):
        self.sid=sid; self.mode=mode; self.app=app
        self.quiz=Quiz(app['cfg']['quiz_code']); self.ws=None; self.task=None; self.jobs=set()
        self.lock=asyncio.Lock(); self.close_lock=asyncio.Lock()
        self.seen=set(); self.delegations=set(); self.inputs=[]; self.consumed=-1
        self.status='dialing' if mode=='sip' else 'connected'
        self.error=''; self.usage=None; self.created=time.monotonic(); self.finished_at=None
        self.ended=False
    def state(self):
        return dict(self.quiz.snapshot(), connection=self.status, mode=self.mode, error=self.error, usage=self.usage)
    async def send(self, typ, **fields):
        if not self.ws or self.ws.closed: raise ConnectionError('Sideband closed')
        await self.ws.send_json(dict(type=typ,event_id=eid(),**fields))
    async def handle(self,e):
        eventid=e.get('event_id'); typ=e.get('type')
        if eventid and eventid in self.seen: return
        if eventid: self.seen.add(eventid)
        if typ=='transport.ringing' and self.status=='dialing': self.status='ringing'
        elif typ=='transport.answered' and not self.ended: self.status='connected'
        elif typ=='transport.failed':
            self.status='failed'; self.error='SIP transport failed'; self.ended=True
        elif typ=='session.input_transcript.delta':
            self.inputs.append(e); self.inputs=self.inputs[-2000:]
        elif typ=='session.delegation.created' and e.get('delegation',{}).get('target')=='client':
            d=e['delegation']['id']
            if d in self.delegations: return
            self.delegations.add(d)
            job=asyncio.create_task(self.delegate(d,e['offset_ms']))
            self.jobs.add(job); job.add_done_callback(self.jobs.discard)
        elif typ=='session.closed':
            self.status='closed'; self.usage=e.get('usage'); self.ended=True
        elif typ=='error': self.error='Live API event error'
    async def delegate(self,d,offset):
        try:
            await self.send('session.thinking.append',delegation_id=d,content='')
            async with self.lock:
                await asyncio.sleep(.6)
                if self.status!='connected': return
                relevant=[x for x in self.inputs if x['end_ms']>self.consumed and x['start_ms']<=offset and x['end_ms']<=offset+350]
                relevant.sort(key=lambda x:(x['start_ms'],x['end_ms']))
                cluster=[]
                for x in relevant:
                    if cluster and x['start_ms']-cluster[-1]['end_ms']>1800: cluster=[]
                    cluster.append(x)
                utterance=''.join(x['delta'] for x in cluster).strip()
                if relevant: self.consumed=max(self.consumed,max(x['end_ms'] for x in relevant))
                self.inputs=[x for x in self.inputs if x['end_ms']>self.consumed]
                result=self.quiz.advance(utterance) if utterance else 'Backend nemá novou odpověď. Požádej o zopakování.'
                await self.send('session.commentary.append',delegation_id=d,content=result)
                if self.quiz.stage=='finished' and self.finished_at is None: self.finished_at=time.monotonic()
        except Exception as exc:
            self.error='Delegation: '+type(exc).__name__
    async def reader(self):
        try:
            async for msg in self.ws:
                if msg.type==WSMsgType.TEXT: await self.handle(json.loads(msg.data))
                elif msg.type==WSMsgType.ERROR: break
        except Exception as exc: self.error='Sideband: '+type(exc).__name__
        finally:
            if not self.ended and self.status!='closing': self.status='disconnected'
    async def close(self):
        async with self.close_lock:
            if not self.ended:
                self.status='closing'
                try:
                    if self.mode in ('sip','sip_inbound'):
                        async with self.app['http'].post(f'{API}/{self.sid}/hangup',headers=self.app['auth'],timeout=ClientTimeout(total=10)) as r:
                            if r.status not in (200,404): raise RuntimeError(f'Hangup HTTP {r.status}')
                    else: await self.send('session.close')
                except Exception:
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

@web.middleware
async def guard(req, handler):
    if req.path not in ('/','/healthz','/webhooks/openai'):
        expected=req.app['cfg']['operator_token']
        supplied=req.headers.get('Authorization','')
        if not secrets.compare_digest(supplied.encode(),('Bearer '+expected).encode()):
            raise web.HTTPUnauthorized(text='Operator token required')
        if req.method=='POST' and req.headers.get('Origin')!=req.app['cfg']['public_origin']:
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
    return web.json_response(dict(model=cfg['model'],demo_code=cfg['quiz_code'],sip_enabled=bool(cfg.get('sip')),sip_inbound_enabled=cfg.get('sip_inbound',{}).get('enabled',False),max_call_seconds=cfg['max_call_seconds']))
def session_payload(cfg, t):
    greeting='Po spojení zahaj pozdravem, představ fiktivní demo a zeptej se na souhlas.'
    session=dict(model=cfg['model'],instructions=PROMPT,audio={'output':{'voice':cfg['voice']}},
        delegation={'type':'client','acknowledgement_delay_ms':800},
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

async def inbound_worker(app,sid):
    """Serialize acceptance with browser/outbound calls; do not trust caller headers."""
    async with app['start_lock']:
        if any(not c.ended for c in app['calls'].values()):
            async with app['http'].post(f'{API}/{sid}/reject',headers=app['auth'],
                    json={'status_code':486},timeout=ClientTimeout(total=10)) as r:
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
            async with app['http'].post(f'{API}/{sid}/accept',headers=app['auth'],
                    json=inbound_payload(app['cfg']),timeout=ClientTimeout(total=12)) as r:
                if r.status!=200:
                    call.status='failed'; call.error=f'Accept HTTP {r.status}'
                    call.ended=True
                    return
            call.status='connected'
            call.ws=await app['http'].ws_connect(f'{WS}/{sid}/attach',headers=app['auth'],
                    heartbeat=20,max_msg_size=4*1024*1024)
            call.task=asyncio.create_task(call.reader())
        except Exception as exc:
            call.error='Inbound setup: '+type(exc).__name__
            # Acceptance may have succeeded even if its response was lost.
            await call.close()
            if call.ended:
                call.status='failed'

async def webhook(req):
    cfg=req.app['cfg'].get('sip_inbound',{})
    if not cfg.get('enabled'): raise web.HTTPNotFound()
    body=await req.read()
    if not verify_webhook(body,req.headers,cfg['webhook_secret']):
        raise web.HTTPUnauthorized(text='Invalid webhook signature')
    try:
        event=json.loads(body)
        if not isinstance(event,dict): raise ValueError()
    except (ValueError,UnicodeError):
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
    if any(key in seen for key in keys): return web.Response(status=204)
    if len(seen)>=20000 or len(req.app['inbound_jobs'])>=16:
        raise web.HTTPServiceUnavailable(text='Inbound capacity reached')
    for key in keys: seen[key]=now+73*3600
    async def work():
        try:
            await inbound_worker(req.app,sid)
        except Exception as exc:
            # No credentials or caller data in logs.
            import logging
            logging.getLogger(__name__).warning('Inbound decision failed: %s',type(exc).__name__)
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
    except (ValueError,TypeError) as exc: raise web.HTTPBadRequest(text=str(exc))
    async with req.app['start_lock']:
        if any(not c.ended for c in req.app['calls'].values()):
            raise web.HTTPConflict(text='An active call already exists; stop it first')
        req.app['calls'].clear()
        http=req.app['http']; cfg=req.app['cfg']; call=None
        try:
            async with http.post(API,headers=req.app['auth'],json=session_payload(cfg,t)) as r:
                if r.status>=300: raise web.HTTPBadGateway(text=f'OpenAI HTTP {r.status}')
                body=await r.json()
            sid=body['session']['id']; call=Call(sid,t['type'],req.app)
            token=secrets.token_urlsafe(24); req.app['calls'][token]=call
            call.ws=await http.ws_connect(f'{WS}/{sid}/attach',headers=req.app['auth'],heartbeat=20,max_msg_size=4*1024*1024)
            call.task=asyncio.create_task(call.reader())
            result=dict(token=token,session_id=sid,mode=t['type'])
            if t['type']=='webrtc': result['sdp']=body['transport']['sdp']
            return web.json_response(result)
        except web.HTTPException: raise
        except Exception as exc:
            if call: await call.close()
            raise web.HTTPBadGateway(text='Session setup failed: '+type(exc).__name__)
def get_call(req):
    c=req.app['calls'].get(req.match_info['token'])
    if not c: raise web.HTTPNotFound(text='Session not found')
    return c
async def status(req): return web.json_response(get_call(req).state())
async def stop(req):
    async with req.app['start_lock']:
        c=get_call(req); await c.close(); return web.json_response(c.state())
async def active(req):
    return web.json_response({'calls':[dict(token=t,**c.state()) for t,c in req.app['calls'].items()]})
async def lifecycle(app):
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
                    await c.close()
    task=asyncio.create_task(reap())
    yield
    task.cancel(); await asyncio.gather(task,return_exceptions=True)
    if app['inbound_jobs']:
        await asyncio.gather(*list(app['inbound_jobs']),return_exceptions=True)
    for c in app['calls'].values(): await c.close()
    await app['http'].close()

def create_app(cfg):
    app=web.Application(middlewares=[guard],client_max_size=150000)
    app['cfg']=cfg; app['auth']={'Authorization':'Bearer '+cfg['openai_api_key']}
    app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/',index),web.get('/healthz',health),web.get('/config',config),
        web.post('/webhooks/openai',webhook),web.post('/session',start),web.get('/active',active),web.get('/state/{token}',status),web.post('/stop/{token}',stop)])
    return app
if __name__=='__main__':
    web.run_app(create_app(load()),host=os.getenv('BIND_HOST','127.0.0.1'),port=int(os.getenv('PORT','8765')),access_log=None)
