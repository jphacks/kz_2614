"""HTTP clients still obtain all required web assets after the source split."""
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from server.api.server import DashboardHTTPServer


class PublicLayoutTests(unittest.TestCase):
    def setUp(self):
        self.server = DashboardHTTPServer(('127.0.0.1', 0))
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def test_browser_and_worklets_remain_available(self):
        expected = {
            '/': b'voice_call.js',
            '/voice_call.js': b'AudioWorkletNode',
            '/dashboard.js': b'fetch',
            '/voice_pcm_worklet.js': b'registerProcessor',
            '/voice_pcm_output_worklet.js': b'registerProcessor',
            '/sounds/heartbeat.ogg': b'OggS',
        }
        for path, marker in expected.items():
            with self.subTest(path=path), urlopen(self.base + path, timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(marker, response.read())

    def test_source_outside_web_root_is_not_served(self):
        for path in ['/server/.env', '/%2e%2e/%2e%2e/server/.env', '/CONTRIBUTING.md']:
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(self.base + path, timeout=2)
                self.assertEqual(caught.exception.code, 404)
