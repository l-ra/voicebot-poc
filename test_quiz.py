import unittest
from quiz import Quiz,BANK
from settings import transport
from server import session_payload

class TestQuiz(unittest.TestCase):
    def game(self):
        q=Quiz(questions=BANK[:3]); q.advance('yes'); q.advance('ready'); return q
    def test_bank(self):
        self.assertEqual(len(BANK),100)
        self.assertEqual(len({q['id'] for q in BANK}),100)
        self.assertEqual(len({q['question'] for q in BANK}),100)
        self.assertTrue(all(q['answer'] and q['hint'] for q in BANK))
    def test_win(self):
        q=self.game()
        for _ in range(3): q.advance('correct')
        self.assertEqual(q.stage,'prize')
        self.assertEqual(q.snapshot()['score'],3)
        self.assertIn('v šest po válce u Kalicha',q.advance('accept'))
        self.assertEqual(q.reason,'accepted')
    def test_refusal(self):
        q=Quiz();q.advance('no');self.assertEqual(q.reason,'declined')
        q=self.game()
        for _ in range(3): q.advance('correct')
        q.advance('decline');self.assertEqual(q.reason,'prize_declined')
    def test_hint(self):
        q=self.game(); name=q.decision_name
        q.advance('incorrect');self.assertTrue(q.hinted)
        self.assertNotEqual(name,q.decision_name)
        q.advance('unclear');q.advance('repeat_question')
        self.assertEqual(q.stage,'question')
        q.advance('correct');self.assertFalse(q.hinted)
        self.assertEqual(len(q.answers),1)
        q.advance('incorrect');q.advance('incorrect')
        self.assertEqual(q.reason,'incorrect')
    def test_request_hint(self):
        q=self.game();q.advance('request_hint');q.advance('request_hint')
        self.assertEqual(q.stage,'question')
        q.advance('give_up');self.assertEqual(q.reason,'incorrect')
    def test_classifier(self):
        q=self.game()
        self.assertIn(q.current['answer'],q.classifier()['instructions'])
        self.assertEqual(q.delegation()['type'],'decisions')
        self.assertNotIn('answer',q.snapshot())
    def test_cancel(self):
        q=self.game();q.advance('end_quiz');self.assertEqual(q.reason,'cancelled')

class TestTransport(unittest.TestCase):
    cfg=dict(model='gpt-live-1.1',voice='marin',sip=dict(provider_url='sips:example.invalid',caller_number='+420602123456',username='test',password='fake'))
    def test_sip(self):
        for d in ('602123456','+420602123456','00420602123456','602 123 456'):
            t=transport({'mode':'sip','destination':d},self.cfg)
            self.assertEqual(t['destination'],'+420602123456')
            self.assertEqual(t['trunk']['auth']['type'],'digest')
        p=session_payload(self.cfg,t)
        self.assertEqual(p['session']['delegation']['type'],'decisions')
        self.assertNotIn('client',p['session'])
    def test_reject(self):
        for d in ['112','+12025550102','sip:example.invalid','+420123']:
            with self.assertRaises(ValueError):transport({'mode':'sip','destination':d},self.cfg)
        with self.assertRaises(ValueError):transport({'mode':'sip','destination':'602123456'},{})
    def test_webrtc(self):
        t=transport({'sdp':'v=0\r\n'},self.cfg)
        self.assertEqual(t['type'],'webrtc')
        self.assertIn('client',session_payload(self.cfg,t)['session'])
        with self.assertRaises(ValueError):transport({'sdp':'bad'},self.cfg)
