import unittest
from quiz import Quiz, BANK, code_from, choice_from
from settings import transport
from server import session_payload

class TestQuiz(unittest.TestCase):
    def test_full(self):
        q=Quiz(questions=BANK[:3])
        q.advance('Ano, chci se zúčastnit'); self.assertEqual(q.stage,'code')
        q.advance('čtyři osm dva šest'); self.assertEqual(q.stage,'question')
        q.advance('béčko');q.advance('Porto');q.advance('C')
        self.assertEqual(q.snapshot()['score'],2)
        q.advance('A');self.assertEqual(len(q.answers),3)
    def test_decline(self):
        q=Quiz();q.advance('Ne, děkuji');self.assertEqual(q.reason,'declined')
    def test_wrong_code(self):
        q=Quiz();q.advance('ano');q.advance('nevím');self.assertEqual(q.attempts,0)
        for _ in range(3):q.advance('1234')
        self.assertEqual(q.reason,'verification_failed')
    def test_unclear(self):
        q=Quiz();q.advance('ano');q.advance('4826');q.advance('A nebo B')
        self.assertEqual(q.snapshot()['answered'],0);q.advance('chci skončit');self.assertEqual(q.reason,'cancelled')
    def test_parsing(self):
        self.assertEqual(code_from('Kód je 4 8 2 6'),'4826')
        self.assertIsNone(code_from('1234 nebo 5678'))
        self.assertEqual(choice_from('Myslím, že B, Merkur',BANK[0][1]),1)
        self.assertIsNone(choice_from('A, nebo B',BANK[0][1]))

class TestTransport(unittest.TestCase):
    cfg=dict(model='gpt-live-1.1',voice='marin',sip=dict(provider_url='sips:example.invalid',caller_number='+12025550100',username='test',password='fake',allowed_destinations=['+12025550101']))
    def test_sip(self):
        t=transport({'mode':'sip','destination':'+12025550101'},self.cfg)
        self.assertEqual(t['trunk']['auth']['type'],'digest')
        p=session_payload(self.cfg,t)
        self.assertNotIn('client',p['session']);self.assertNotIn('format',p['session']['audio'])
    def test_reject(self):
        for d in ['112','+12025550102','sip:example.invalid']:
            with self.assertRaises(ValueError):transport({'mode':'sip','destination':d},self.cfg)
        with self.assertRaises(ValueError):transport({'mode':'sip','destination':'+12025550101'},{})
    def test_webrtc(self):
        t=transport({'sdp':'v=0\r\n'},self.cfg)
        self.assertEqual(t['type'],'webrtc')
        self.assertIn('client',session_payload(self.cfg,t)['session'])
        with self.assertRaises(ValueError):transport({'sdp':'bad'},self.cfg)
if __name__=='__main__':unittest.main()
