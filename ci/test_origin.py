import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from settings import load

class OriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'config.json'
        self.cfg = dict(openai_api_key='dummy', operator_token='x'*32, quiz_code='4826')
    def read(self, env):
        self.path.write_text(json.dumps(self.cfg))
        with patch.dict(os.environ, env, clear=True):
            return load(self.path)
    def test_ingress_without_json_origin(self):
        self.assertEqual(self.read({'VOICEBOT_PUBLIC_ORIGIN':'https://voicebot.example.org'})['public_origin'], 'https://voicebot.example.org')
    def test_ingress_overrides_json(self):
        self.cfg['public_origin'] = 'http://localhost:8765'
        self.assertEqual(self.read({'VOICEBOT_PUBLIC_ORIGIN':'https://voicebot.example.org'})['public_origin'], 'https://voicebot.example.org')
        self.assertEqual(self.read({})['public_origin'], 'http://localhost:8765')
    def test_missing_origin(self):
        with self.assertRaises(ValueError): self.read({})
