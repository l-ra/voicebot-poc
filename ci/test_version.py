import unittest
from version import resolve
from registry import stable

class VersionTests(unittest.TestCase):
    def test_dev(self):
        self.assertEqual(resolve('refs/heads/main','push','12','2')['version'],'0.0.0-dev.12.2')
    def test_release(self):
        self.assertEqual(resolve('refs/tags/v0.3.0','push','1','1')['prerelease'],'false')
        self.assertEqual(resolve('refs/tags/v0.4.0-rc.1','push','1','1')['prerelease'],'true')
    def test_invalid(self):
        for ref,event in [('refs/tags/v01.2.3','push'),('refs/tags/v0.0.0','push'),('refs/tags/v1.2.3','workflow_dispatch'),('refs/heads/feature','push'),('refs/heads/main','pull_request')]:
            with self.assertRaises(ValueError): resolve(ref,event,'1','1')
    def test_stable_order(self):
        self.assertGreater(stable('0.10.0'),stable('0.9.9'))
        self.assertIsNone(stable('0.4.0-rc.1'))
