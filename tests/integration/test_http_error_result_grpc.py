"""Verify HTTP errors are serialized as error Result events over gRPC."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import grpc
import pytest
from syntara_node_http_request.__main__ import STEP_IDENTITY, create_bindings
from syntara_plugin.runtime import add_container_runtime_service, step_registry_from_bindings
from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

from tests.integration.helpers import execute_events

pytestmark = pytest.mark.integration


def test_http_timeout_and_503_errors_are_serialized_in_result_event() -> None:
    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/timeout":
                time.sleep(2)
                try:
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except OSError:
                    # The client has already timed out and closed the connection.
                    pass
                return

            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream_thread = ThreadPoolExecutor(max_workers=1)
    serve_future = upstream_thread.submit(upstream.serve_forever)
    bindings = create_bindings(Path(__file__).resolve().parents[2] / "http-request/manifest.yaml")
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    add_container_runtime_service(server, step_registry_from_bindings(bindings))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    base_url = f"http://upstream.example:{upstream.server_port}"

    cases = (
        ("timeout", 1, "TIMEOUT", "timeout"),
        ("unavailable", 10, "UPSTREAM_TRANSIENT", "dependency"),
    )

    try:
        with (
            grpc.insecure_channel(f"127.0.0.1:{port}") as channel,
            # Permit the local test server while retaining the real HTTP and gRPC paths.
            patch(
                "syntara_node_http_request.executor._validate_url_no_ssrf",
                side_effect=lambda url: (url, "127.0.0.1"),
            ),
        ):
            stub = container_pb2_grpc.ContainerServiceStub(channel)
            for path, http_timeout, expected_code, expected_category in cases:
                request = container_pb2.ExecuteRequest(
                    invocation_id=f"http-error-{path}",
                    workflow_run_id="wf-http-error",
                    step_revision_id=STEP_IDENTITY,
                    runtime_abi="syntara.container/v1alpha1",
                    inputs_json=json.dumps(
                        {"url": f"{base_url}/{path}", "timeout_seconds": http_timeout}
                    ).encode(),
                    runtime_context_json=b"{}",
                    timeout_seconds=10,
                )
                events = list(execute_events(stub, request, timeout=12))
                results = [event.result for event in events if event.HasField("result")]
                failures = [event.failure for event in events if event.HasField("failure")]

                assert not failures, f"Expected an error Result event, got {failures}"
                assert len(results) == 1
                result = results[0]
                assert result.status_code != 0
                error = json.loads(result.error_result_json)
                assert error["code"] == expected_code
                assert error["category"] == expected_category
                assert error["retryable"] is True
    finally:
        server.stop(0).wait()
        upstream.shutdown()
        upstream.server_close()
        serve_future.result(timeout=2)
        upstream_thread.shutdown(wait=True)
