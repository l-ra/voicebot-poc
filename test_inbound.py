import unittest
from aiohttp.test_utils import TestClient,TestServer
from server import create_app

class InboundTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_even_with_old_config(self):
        cfg=dict(openai_api_key='dummy',operator_token='x'*32,public_origin='https://example.org',model='gpt-live-1.1',voice='marin',max_call_seconds=300,sip_inbound=dict(enabled=True))
        async with TestClient(TestServer(create_app(cfg))) as client:
            r=await client.post('/webhooks/openai',json={'type':'live.transport.incoming'})
            self.assertEqual(r.status,404)
            r=await client.get('/active')
            self.assertEqual(r.status,401)
