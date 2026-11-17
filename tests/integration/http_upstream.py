"""Small controlled HTTP upstream used by the HTTP step image tests."""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

cancelled = False
started = False


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, document: object) -> None:
        payload = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        global cancelled, started
        parsed = urlsplit(self.path)
        if parsed.path == "/get":
            args = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            self._json(200, {"args": args})
            return
        if parsed.path == "/timeout":
            time.sleep(2.5)
            self._json(200, {"delayed": True})
            return
        if parsed.path == "/unavailable":
            self._json(503, {"error": "upstream unavailable"})
            return
        if parsed.path == "/cancel":
            started = True
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"x")
            self.wfile.flush()
            self.connection.settimeout(15)
            try:
                cancelled = self.connection.recv(1) == b""
            except TimeoutError:
                pass
            except OSError:
                cancelled = True
            return
        if parsed.path == "/state":
            self._json(200, {"started": started, "cancelled": cancelled})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/post":
            self._json(404, {"error": "not found"})
            return
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size))
        self._json(200, {"json": body})

    def log_message(self, format: str, *args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    daemon_threads = True


Server(("0.0.0.0", 8000), Handler).serve_forever()
