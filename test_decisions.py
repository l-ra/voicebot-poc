import unittest
from unittest.mock import AsyncMock
from server import Call
from quiz import Quiz,BANK

class DecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.call=Call('live_test','webrtc',{'cfg':{'decision_min_confidence':.8}},Quiz(BANK[:3]))
        self.call.confirmed=AsyncMock(return_value={'end_ms':100})
    async def decision(self,choice,confidence=.99,d='del_1',offset=10):
        c=self.call
        await c.handle({'type':'session.delegation.created','event_id':d+'_start','offset_ms':offset,'delegation':{'id':d,'target':'decisions'}})
        await c.decide(d,{'answers':[{'type':'choice','name':c.quiz.decision_name,'choice':choice,'confidence':confidence}]})
    async def test_confirm_before_commentary(self):
        await self.decision('yes')
        c=self.call
        self.assertEqual(c.quiz.stage,'name')
        self.assertEqual([x.args[0] for x in c.confirmed.await_args_list],['session.update','session.commentary.append'])
        self.assertEqual(c.confirmed.await_args_list[1].kwargs['delegation_id'],'del_1')
        await self.decision('ready',d='del_2',offset=200)
        self.assertEqual(c.quiz.stage,'question')
    async def test_low_confidence(self):
        await self.decision('yes',.4)
        self.assertEqual(self.call.quiz.stage,'consent')
    async def test_stale(self):
        await self.decision('yes')
        await self.decision('ready',d='del_2',offset=50)
        self.assertEqual(self.call.quiz.stage,'name')
    async def test_update_failure(self):
        self.call.confirmed.side_effect=TimeoutError()
        self.call.send=AsyncMock()
        await self.decision('yes')
        self.assertEqual(self.call.quiz.reason,'technical_error')
