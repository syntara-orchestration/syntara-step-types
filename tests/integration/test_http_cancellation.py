"""Exercise HTTP cancellation through the real SDK gRPC service with a local upstream."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import grpc
import pytest
from syntara_node_http_request.__main__ import STEP_IDENTITY, create_bindings
from syntara_plugin.runtime import (
    add_container_runtime_service,
    step_registry_from_bindings,
)
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

pytestmark = pytest.mark.integration


def test_http_cancel_stops_io_and_keeps_control_rpcs_available():
    started = threading.Event()
    stopped = threading.Event()

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.flush()
            started.set()
            self.connection.settimeout(3)
            # The client is waiting for the response body. Cancellation should
            # close its TCP connection rather than leave a background request.
            if self.connection.recv(1) == b"":
                stopped.set()

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    url = f"http://upstream.example:{upstream.server_port}/"

    bindings = create_bindings(Path(__file__).resolve().parents[2] / "http-request/manifest.yaml")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as dispatch:
        server = grpc.server(dispatch)
        add_container_runtime_service(server, step_registry_from_bindings(bindings))
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            with (
                grpc.insecure_channel(f"127.0.0.1:{port}") as channel,
                # Allow this test's local upstream only; exercise real HTTP and gRPC I/O.
                patch(
                    "syntara_node_http_request.executor._validate_url_no_ssrf",
                    return_value=(url, "127.0.0.1"),
                ),
                concurrent.futures.ThreadPoolExecutor(max_workers=1) as caller,
            ):
                stub = container_pb2_grpc.ContainerServiceStub(channel)
                request = container_pb2.ExecuteRequest(
                    invocation_id="http-cancel-regression",
                    workflow_run_id="wf-cancel",
                    step_revision_id=STEP_IDENTITY,
                    runtime_abi="syntara.container/v1alpha1",
                    inputs_json=json.dumps({"url": url}).encode(),
                    runtime_context_json=b"{}",
                    credential_file_paths_json=b"{}",
                    timeout_seconds=5,
                )
                result = caller.submit(lambda: list(stub.Execute(request, timeout=5)))
                assert started.wait(2), "HTTP request never reached the transport"
                response = stub.Cancel(
                    container_pb2.CancelRequest(invocation_id=request.invocation_id), timeout=0.5
                )
                assert response.accepted
                events = result.result(timeout=1)
                assert events[-1].failure.code == "CANCELLED"
                assert stopped.wait(1), "HTTP I/O continued after cancellation"
                assert stub.Health(container_pb2.HealthRequest(), timeout=0.5).ready
        finally:
            server.stop(0).wait()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=2)
