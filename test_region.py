import unittest
from settings import openai_endpoints
from server import create_app

class RegionTests(unittest.TestCase):
    def test_endpoints(self):
        for region,host in [('global','api.openai.com'),('us','us.api.openai.com'),('eu','eu.api.openai.com')]:
            with self.subTest(region=region):
                self.assertEqual(openai_endpoints(region),(f'https://{host}/v1/live/sessions',f'wss://{host}/v1/live/sessions'))
                app=create_app({'openai_api_key':'dummy','openai_region':region})
                self.assertEqual((app['api_url'],app['ws_url']),openai_endpoints(region))
    def test_default(self):
        app=create_app({'openai_api_key':'dummy'})
        self.assertEqual(app['api_url'],openai_endpoints('global')[0])
    def test_invalid(self):
        for region in ('US','invalid','https://evil.example',None,[]):
            with self.assertRaises(ValueError): openai_endpoints(region)
