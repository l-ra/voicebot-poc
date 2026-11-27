import unittest
from quiz import Quiz, BANK, code_from, choice_from
class TestQuiz(unittest.TestCase):
    def new(self): return Quiz(questions=BANK[:3])
    def test_full(self):
        q=self.new(); q.advance('Ano, chci se zúčastnit'); self.assertEqual(q.stage,'code'); q.advance('čtyři osm dva šest'); self.assertEqual(q.stage,'question')
        q.advance('béčko'); q.advance('Porto'); q.advance('C'); self.assertEqual(q.stage,'finished'); self.assertEqual(q.snapshot()['score'],2); self.assertEqual(q.reason,'completed');q.advance('A');self.assertEqual(len(q.answers),3)
    def test_invalid_and_decline(self):
        q=self.new(); q.advance('Ne, děkuji'); self.assertEqual(q.reason,'declined');q=self.new();q.advance('ano');q.advance('nevím');self.assertEqual(q.attempts,0)
        for _ in range(3):q.advance('1234')
        self.assertEqual(q.reason,'verification_failed')
    def test_unclear(self):
        q=self.new();q.advance('ano');q.advance('4826');q.advance('A nebo B');self.assertEqual(q.snapshot()['answered'],0);q.advance('chci skončit');self.assertEqual(q.reason,'cancelled')
    def test_parsing(self):
        self.assertEqual(code_from('Kód je 4 8 2 6'), '4826');self.assertIsNone(code_from('1234 nebo 5678'));self.assertEqual(choice_from('Myslím, že B, Merkur',BANK[0][1]),1);self.assertIsNone(choice_from('A, nebo B',BANK[0][1]))
if __name__=='__main__':unittest.main()
