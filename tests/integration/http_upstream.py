"""Small controlled HTTP upstream used by the HTTP step image tests."""

from __future__ import annotations

import json
import os
import ssl
import threading
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
        if parsed.path == "/redirect/same-origin":
            self.send_response(302)
            self.send_header("Location", "/get?via=redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/redirect/private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8000/private")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/authorization":
            received = self.headers.get_all("Authorization", [])
            self._json(
                200,
                {
                    "authorizationCount": len(received),
                    "authorizationMatchesExpected": received
                    == [f"Bearer {os.environ.get('EXPECTED_BEARER_TOKEN')}"],
                },
            )
            return
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


def _serve_https() -> None:
    server = Server(("0.0.0.0", 8443), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(os.environ["TLS_CERT_FILE"], os.environ["TLS_KEY_FILE"])
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


threading.Thread(target=_serve_https, daemon=True).start()
Server(("0.0.0.0", 8000), Handler).serve_forever()
