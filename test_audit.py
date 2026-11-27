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
        r=Mock(status=401,headers={'x-request-id':'req_test'})
        r.content.read=AsyncMock(return_value=json.dumps({'error':{'message':'Invalid sk-test-secret','code':'invalid_api_key','type':'invalid_request_error'},'other':'private'}).encode())
        with self.assertLogs('voicebot',level='ERROR') as logs:
            info=await api_response('accept',r,self.cfg,session_id='live_test')
        self.assertEqual(info['request_id'],'req_test')
        self.assertEqual(info['error_code'],'invalid_api_key')
        self.assertNotIn('sk-test-secret',str(logs.output))
        self.assertNotIn('private',str(logs.output))
    async def test_success_not_read(self):
        r=Mock(status=200,headers={})
        r.content.read=AsyncMock()
        await api_response('accept',r,self.cfg)
        r.content.read.assert_not_awaited()
