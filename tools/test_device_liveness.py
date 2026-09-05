import io
import json
import unittest
from unittest.mock import patch

from device_liveness import fetch_devices


class DeviceLivenessTests(unittest.TestCase):
    def test_pages_past_server_limit_without_reading_backups(self):
        pages = [[{"install_id": "a"}], [{"install_id": "b"}], []]
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(json.dumps(page).encode()) for page in pages
        ]) as request:
            self.assertEqual(fetch_devices("https://example.test", "test-key"), [
                {"install_id": "a"}, {"install_id": "b"}
            ])
        urls = [call.args[0].full_url for call in request.call_args_list]
        self.assertEqual(len(urls), 3)
        self.assertTrue(all("/install_heartbeats?" in url for url in urls))
        self.assertIn("install_id=gt.a", urls[1])
        self.assertIn("install_id=gt.b", urls[2])
        self.assertTrue(all("payload" not in url and "device_name" not in url for url in urls))


if __name__ == "__main__":
    unittest.main()
