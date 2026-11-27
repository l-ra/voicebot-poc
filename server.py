import asyncio, json, os, secrets, time, uuid, contextlib
from pathlib import Path
from aiohttp import web, ClientSession, ClientTimeout, WSMsgType
from quiz import Quiz, PROMPT

ROOT=Path(__file__).resolve().parent
KEY=os.getenv('OPENAI_API_KEY','')
MODEL=os.getenv('OPENAI_LIVE_MODEL','gpt-live-1.1')
API='https://api.openai.com/v1/live/sessions'
WS='wss://api.openai.com/v1/live/sessions'
PORT=int(os.getenv('PORT','8765'))
CODE=os.getenv('QUIZ_CODE','4826')
AUTH={'Authorization':f'Bearer {KEY}'}

def eid(): return 'evt_'+uuid.uuid4().hex

class Call:
    def __init__(self, sid):
        self.sid=sid; self.quiz=Quiz(CODE); self.ws=None; self.task=None; self.jobs=set(); self.lock=asyncio.Lock()
        self.seen=set(); self.delegations=set(); self.inputs=[]; self.consumed=-1; self.status='connecting'; self.error=''; self.usage=None; self.created=time.monotonic()
    def state(self): return dict(self.quiz.snapshot(), connection=self.status, error=self.error, usage=self.usage)
    async def send(self, typ, **fields):
        if self.ws and not self.ws.closed: await self.ws.send_json(dict(type=typ,event_id=eid(),**fields))
    async def handle(self,e):
        eventid=e.get('event_id'); typ=e.get('type')
        if eventid and eventid in self.seen: return
        if eventid: self.seen.add(eventid)
        if typ=='session.input_transcript.delta': self.inputs.append(e)
        elif typ=='session.delegation.created' and e.get('delegation',{}).get('target')=='client':
            d=e['delegation']['id']
            if d in self.delegations: return
            self.delegations.add(d)
            job=asyncio.create_task(self.delegate(d,e['offset_ms'])); self.jobs.add(job); job.add_done_callback(self.jobs.discard)
        elif typ=='session.closed': self.status='closed'; self.usage=e.get('usage')
        elif typ=='error': self.error=str(e.get('error',{}).get('message','Chyba Live API'))[:300]
    async def delegate(self,d,offset):
        try:
            # Suppress the automatic filler, without promising that generated speech stops.
            await self.send('session.thinking.append',delegation_id=d,content='')
            async with self.lock:
                # The protocol has no transcript-done event. Allow delayed fragments to arrive.
                await asyncio.sleep(.6)
                if self.status!='connected': return
                relevant=[x for x in self.inputs if x['end_ms']>self.consumed and x['start_ms']<=offset and x['end_ms']<=offset+350]
                relevant.sort(key=lambda x:(x['start_ms'],x['end_ms']))
                # Restrict to the last cluster of utterances, not earlier phases.
                cluster=[]
                for x in relevant:
                    if cluster and x['start_ms']-cluster[-1]['end_ms']>1800: cluster=[]
                    cluster.append(x)
                utterance=''.join(x['delta'] for x in cluster).strip()
                if relevant: self.consumed=max(self.consumed,max(x['end_ms'] for x in relevant))
                self.inputs=[x for x in self.inputs if x['end_ms']>self.consumed]
                if not utterance:
                    result='Backend nemá novou jednoznačnou odpověď. Neměň fázi, požádej o zopakování poslední odpovědi.'
                else: result=self.quiz.advance(utterance)
                await self.send('session.commentary.append',delegation_id=d,content=result)
        except Exception as exc:
            self.error=f'Zpracování delegace selhalo: {type(exc).__name__}'
    async def reader(self):
        try:
            async for msg in self.ws:
                if msg.type==WSMsgType.TEXT: await self.handle(json.loads(msg.data))
                elif msg.type==WSMsgType.ERROR: break
        except Exception as exc: self.error=f'Sideband: {type(exc).__name__}'
        finally:
            if self.status!='closed': self.status='disconnected'
    async def close(self):
        if self.status=='connected':
            with contextlib.suppress(Exception): await self.send('session.close')
            if self.task:
                with contextlib.suppress(asyncio.TimeoutError, Exception): await asyncio.wait_for(asyncio.shield(self.task),4)
        if self.ws: await self.ws.close()
        for j in list(self.jobs): j.cancel()
        if self.jobs: await asyncio.gather(*self.jobs,return_exceptions=True)
        if self.task and not self.task.done(): self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        if self.status!='closed': self.status='disconnected'

@web.middleware
async def guard(request, handler):
    # Local development only. Cross-origin pages must not be able to spend the API key.
    if request.host not in {f'localhost:{PORT}',f'127.0.0.1:{PORT}'}: raise web.HTTPForbidden(text='Localhost only')
    if request.method=='POST' and request.headers.get('Origin') not in {f'http://localhost:{PORT}',f'http://127.0.0.1:{PORT}'}: raise web.HTTPForbidden(text='Invalid origin')
    response=await handler(request); response.headers['Cache-Control']='no-store'; return response

async def index(req): return web.FileResponse(ROOT/'index.html')
async def config(req): return web.json_response(dict(model=MODEL,key_configured=bool(KEY),demo_code=CODE))
async def start(req):
    if not KEY: raise web.HTTPServiceUnavailable(text='Nastav OPENAI_API_KEY na serveru.')
    data=await req.json(); sdp=data.get('sdp','')
    if not isinstance(sdp,str) or not sdp.startswith('v=0') or len(sdp)>100000: raise web.HTTPBadRequest(text='Neplatná SDP nabídka')
    async with req.app['start_lock']:
        # One active demonstration; bound lifetime even if the browser disappears.
        for token,old in list(req.app['calls'].items()):
            await old.close(); del req.app['calls'][token]
        config=dict(model=MODEL,instructions=PROMPT,audio={'output':{'voice':'marin'}},
                    delegation={'type':'client','acknowledgement_delay_ms':800},
                    input=[{'type':'message','role':'developer','content':[{'type':'input_text','text':'Hovor právě začal. Zahaj pozdravem, představ demo a zeptej se na souhlas.'}]}],
                    client={'data_channel':{'allowed_client_events':[], 'allowed_server_events':[{'type':'session.started'},{'type':'session.closed'},{'type':'error'}]}})
        http=req.app['http']
        try:
            async with http.post(API,headers=AUTH,json={'session':config,'transport':{'type':'webrtc','sdp':sdp}}) as r:
                if r.status>=300: raise web.HTTPBadGateway(text=f'OpenAI HTTP {r.status}: '+(await r.text())[:800])
                body=await r.json()
            sid=body['session']['id']; answer=body['transport']['sdp']; call=Call(sid)
            call.ws=await http.ws_connect(f'{WS}/{sid}/attach',headers=AUTH,heartbeat=20,max_msg_size=4*1024*1024)
        except web.HTTPException: raise
        except Exception as exc: raise web.HTTPBadGateway(text=f'Vytvoření relace nebo sidebandu selhalo: {type(exc).__name__}')
        call.status='connected'; call.task=asyncio.create_task(call.reader()); token=secrets.token_urlsafe(24); req.app['calls'][token]=call
        return web.json_response(dict(sdp=answer,token=token,session_id=sid))

def get_call(req):
    c=req.app['calls'].get(req.match_info['token'])
    if not c: raise web.HTTPNotFound(text='Relace neexistuje')
    return c
async def status(req): return web.json_response(get_call(req).state())
async def stop(req):
    c=get_call(req); await c.close(); return web.json_response(c.state())
async def lifecycle(app):
    app['http']=ClientSession(timeout=ClientTimeout(total=35)); app['calls']={}; app['start_lock']=asyncio.Lock()
    async def reap():
        while True:
            await asyncio.sleep(5)
            for c in list(app['calls'].values()):
                if c.status=='connected' and time.monotonic()-c.created>300: await c.close()
    task=asyncio.create_task(reap())
    yield
    task.cancel(); await asyncio.gather(task,return_exceptions=True)
    for c in app['calls'].values(): await c.close()
    await app['http'].close()

def create_app():
    app=web.Application(middlewares=[guard],client_max_size=150000); app.cleanup_ctx.append(lifecycle)
    app.add_routes([web.get('/',index),web.get('/config',config),web.post('/session',start),web.get('/state/{token}',status),web.post('/stop/{token}',stop)])
    return app
if __name__=='__main__':
    if len(CODE)!=4 or not CODE.isascii() or not CODE.isdigit(): raise SystemExit('QUIZ_CODE must be four ASCII digits')
    web.run_app(create_app(),host='127.0.0.1',port=PORT,access_log=None)
