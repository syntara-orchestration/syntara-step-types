"""Small controlled fake AAP gateway used by the AAP Workflow step image tests.

Serves the gateway controller API surface the step uses, over HTTPS (8443) and
a plain-HTTP debug channel (8000). Behavior is selected by workflow job
template id:

- 42: launch ok; job 7 polls ``running`` twice, then ``successful`` + artifacts
- 43: launch ok; job 8 finishes ``failed``
- 44: launch rejected with HTTP 401
- 45: launch rejected with HTTP 404
- 47: launch ok; job 9 stays ``running`` forever (cancel/timeout tests)
- 48: launch recorded, then the response is dropped (uncertain-launch test);
  the correlated job is visible to the reconciliation lookup

Every authenticated call must carry ``Authorization: Bearer <AAP_TEST_TOKEN>``.
The plain-HTTP ``/state`` endpoint exposes launch counts, cancel requests, and
token-match results for assertions.
"""

from __future__ import annotations

import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = os.environ.get("AAP_TEST_TOKEN", "aap-test-token")
API = "/api/controller/v2"

_lock = threading.Lock()
_next_job_id = 100
_jobs: dict[int, dict] = {}  # job_id -> {template_id, token, polls, status}
_launch_counts: dict[int, int] = {}
_cancel_requests: list[int] = []
_auth_matches: list[bool] = []


def _check_auth(headers: object) -> bool:
    match = headers.get("Authorization") == f"Bearer {TOKEN}"  # type: ignore[attr-defined]
    with _lock:
        _auth_matches.append(match)
    return match


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, document: object) -> None:
        payload = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _drop(self) -> None:
        """Close the connection without a response (lost launch response)."""
        try:
            self.connection.close()
        except OSError:
            pass

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/state":
            with _lock:
                self._json(
                    200,
                    {
                        "launchCounts": dict(_launch_counts),
                        "cancelRequests": list(_cancel_requests),
                        "authMatches": list(_auth_matches),
                        "jobs": {
                            str(job_id): {
                                "templateId": job["template_id"],
                                "status": job["status"],
                                "polls": job["polls"],
                            }
                            for job_id, job in _jobs.items()
                        },
                    },
                )
            return

        if not _check_auth(self.headers):
            self._json(401, {"error": "unauthorized"})
            return

        if path == f"{API}/workflow_job_templates/":
            args = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
            if args.get("name") == "example-workflow":
                self._json(200, {"results": [{"id": 42}]})
            else:
                self._json(200, {"results": []})
            return
        if path.startswith(f"{API}/workflow_job_templates/") and path.endswith("/jobs/"):
            # The executor's token key must round-trip for reconciliation. A
            # freshly launched job reports as pre-terminal until polling starts.
            with _lock:
                results = [
                    {
                        "id": job["id"],
                        "status": "running" if job["polls"] == 0 else job["status"],
                        "extra_vars": {"_syntara_launch_token": job["token"]},
                    }
                    for job in _jobs.values()
                    if job["template_id"] == 42
                ]
            self._json(200, {"results": results})
            return
        if path == f"{API}/workflow_jobs/":
            self._json(200, {"results": []})
            return

        if path.startswith(f"{API}/workflow_jobs/") and path.endswith("/"):
            job_id = int(path.rstrip("/").rsplit("/", 1)[-1])
            with _lock:
                job = _jobs.get(job_id)
                if job is None:
                    self._json(404, {"error": "job not found"})
                    return
                job["polls"] += 1
                if job["template_id"] == 42 and job["polls"] < 3:
                    status = "running"
                elif job["template_id"] == 47:
                    status = "running"
                else:
                    status = job["status"]
                document = {
                    "id": job_id,
                    "status": status,
                    "artifacts": {"summary": "ok"} if status == "successful" else {},
                    "created": "2026-01-01T00:00:00Z",
                    "started": "2026-01-01T00:00:01Z",
                    "finished": (
                        "2026-01-01T00:00:02Z" if status in ("successful", "failed") else None
                    ),
                }
            self._json(200, document)
            return

        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        global _next_job_id
        parsed = urlsplit(self.path)
        path = parsed.path
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")

        if not _check_auth(self.headers):
            self._json(401, {"error": "unauthorized"})
            return

        if path == f"{API}/workflow_job_templates/44/launch/":
            self._json(401, {"error": "unauthorized"})
            return
        if path == f"{API}/workflow_job_templates/45/launch/":
            self._json(404, {"error": "not found"})
            return
        if path.endswith("/launch/"):
            template_id = int(path.rstrip("/").rsplit("/", 2)[-2])
            with _lock:
                _launch_counts[template_id] = _launch_counts.get(template_id, 0) + 1
                token = (body.get("extra_vars") or {}).get("_syntara_launch_token", "")
                if template_id == 48:
                    # Record the job, then drop the response — the executor must
                    # reconcile by token instead of re-POSTing. The job behaves
                    # like a template-42 job (running twice, then successful).
                    job_id = _next_job_id
                    _next_job_id += 1
                    _jobs[job_id] = {
                        "id": job_id,
                        "template_id": 42,  # visible to the template-42 jobs lookup
                        "token": token,
                        "polls": 0,
                        "status": "successful",
                    }
                    self._drop()
                    return
                job_id = {42: 7, 43: 8, 47: 9}.get(template_id)
                if job_id is None:
                    self._json(404, {"error": "unknown template"})
                    return
                _jobs[job_id] = {
                    "id": job_id,
                    "template_id": template_id,
                    "token": token,
                    "polls": 0,
                    "status": "successful"
                    if template_id == 42
                    else ("failed" if template_id == 43 else "running"),
                }
            self._json(201, {"id": job_id})
            return

        if path.endswith("/cancel/"):
            job_id = int(path.rstrip("/").rsplit("/", 2)[-2])
            with _lock:
                _cancel_requests.append(job_id)
            self._json(202, {"cancelled": True})
            return

        self._json(404, {"error": "not found"})

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
