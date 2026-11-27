import json
import unittest
from unittest.mock import Mock, AsyncMock
from audit import clean, error_fields, api_response

class AuditTests(unittest.IsolatedAsyncioTestCase):
    cfg=dict(openai_api_key='sk-test-secret',operator_token='operator-secret',quiz_code='4826',sip=dict(password='sip-password',username='sip-user',caller_number='+420123456789'),sip_inbound=dict(webhook_secret='whsec_secret'))
    def test_redact(self):
        message='Incorrect API key sk-test-secret / operator-secret / 4826 / sip-password / sip-user / +420123456789 / whsec_secret'
        result=clean(message,self.cfg)
        for word in ('sk-test-secret','operator-secret','4826','sip-password','sip-user','+420123456789','whsec_secret'):
            self.assertNotIn(word,result)
    async def test_http_401(self):
        r=Mock(status=401,headers={'x-request-id':'req_test'},url='https://us.api.openai.com/v1/live/sessions/live_test/accept',history=[])
        r.content.read=AsyncMock(side_effect=[json.dumps({'error':{'message':'Invalid sk-test-secret','code':'invalid_api_key','type':'invalid_request_error'},'other':'private'}).encode(),b''])
        with self.assertLogs('voicebot',level='ERROR') as logs:
            info=await api_response('accept',r,self.cfg,session_id='live_test')
        self.assertEqual(info['request_id'],'req_test')
        self.assertEqual(info['error_code'],'invalid_api_key')
        self.assertNotIn('sk-test-secret',str(logs.output))
        self.assertNotIn('private',str(logs.output))
    async def test_success_not_read(self):
        r=Mock(status=200,headers={},url='https://api.openai.com/v1/models',history=[])
        r.content.read=AsyncMock()
        await api_response('accept',r,self.cfg)
        r.content.read.assert_not_awaited()

    async def test_non_json_404(self):
        for preview in (False,True):
            r=Mock(status=404,url='https://us.api.openai.com/v1/live/sessions/live_test/accept?secret=hidden',history=[],
                   headers={'Server':'cloudflare','Content-Type':'text/plain','CF-Ray':'ray-test','Set-Cookie':'private-cookie','Authorization':'Bearer private'})
            r.content.read=AsyncMock(side_effect=[b'404 page not found sk-test-secret',b''])
            with self.assertLogs('voicebot',level='ERROR') as logs:
                info=await api_response('accept',r,dict(self.cfg,diagnostic_body_preview=preview))
            self.assertEqual(info['response_headers']['server'],'cloudflare')
            self.assertEqual(info['error_body'],'not_json')
            self.assertEqual('error_body_preview' in info,preview)
            self.assertNotIn('private-cookie',str(logs.output))
            self.assertNotIn('sk-test-secret',str(logs.output))
            self.assertNotIn('secret=hidden',str(logs.output))
    async def test_redirect(self):
        r=Mock(status=307,url='https://us.api.openai.com/v1/models',history=[],
               headers={'Location':'https://example.org/path?token=private'})
        r.content.read=AsyncMock(return_value=b'')
        info=await api_response('models_probe',r,self.cfg)
        self.assertEqual(info['response_headers']['location'],'https://example.org/path')
