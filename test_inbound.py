import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from server import create_app, verify_webhook, inbound_payload

KEY=b'test-key-32-bytes-long-for-webhook'
SECRET='whsec_'+base64.b64encode(KEY).decode()
def signed(body,stamp=None,ident='wh_test'):
    stamp=str(int(time.time()) if stamp is None else stamp)
    sig=base64.b64encode(hmac.digest(KEY,ident.encode()+b'.'+stamp.encode()+b'.'+body,'sha256')).decode()
    return {'webhook-id':ident,'webhook-timestamp':stamp,'webhook-signature':'v1,'+sig}
def event(sid='live_test'):
    return json.dumps({'type':'live.transport.incoming','data':{'type':'sip','session_id':sid}}).encode()

class InboundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg=dict(openai_api_key='dummy',operator_token='x'*32,quiz_code='4826',public_origin='https://example.org',model='gpt-live-1.1',voice='marin',max_call_seconds=300,sip_inbound=dict(enabled=True,webhook_secret=SECRET))
        self.client=TestClient(TestServer(create_app(self.cfg)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
    async def test_signed_dedup(self):
        body=event()
        with patch('server.inbound_worker',new_callable=AsyncMock) as worker:
            for ident in ('wh_1','wh_1','wh_2'):
                r=await self.client.post('/webhooks/openai',data=body,headers=signed(body,ident=ident))
                self.assertEqual(r.status,204)
            import asyncio
            await asyncio.sleep(0)
            worker.assert_awaited_once()
    async def test_invalid_signature(self):
        body=event()
        r=await self.client.post('/webhooks/openai',data=body+b' ',headers=signed(body))
        self.assertEqual(r.status,401)
        self.assertFalse(verify_webhook(body,signed(body,int(time.time())-301),SECRET))
    async def test_disabled(self):
        self.cfg['sip_inbound']['enabled']=False
        body=event()
        r=await self.client.post('/webhooks/openai',data=body,headers=signed(body))
        self.assertEqual(r.status,404)
    async def test_operator_still_required(self):
        r=await self.client.get('/active')
        self.assertEqual(r.status,401)
    def test_payload(self):
        p=inbound_payload(self.cfg)
        self.assertEqual(p['session']['type'],'live')
        self.assertNotIn('transport',p)
        self.assertNotIn('client',p['session'])
