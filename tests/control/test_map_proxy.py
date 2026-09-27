from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
from unittest.mock import patch

from rescuebot import web


class MapProxyTests(unittest.TestCase):
    def test_relays_the_map_viewer_and_reports_503_when_it_is_down(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = b'{"width": 3}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        with patch.object(web, "MAP_URL", f"http://127.0.0.1:{server.server_port}"):
            reply = web._fetch_map("/map.json")
        self.assertEqual((reply.status_code, reply.body), (200, b'{"width": 3}'))

        with patch.object(web, "MAP_URL", "http://127.0.0.1:9"):
            reply = web._fetch_map("/map.json")
        self.assertEqual(reply.status_code, 503)


if __name__ == "__main__":
    unittest.main()
