#!/usr/bin/env python3
"""Simple test client for calling containerized steps via gRPC.

This script demonstrates how to call the actual script and HTTP request containers
without integrating into Syntara's activity system yet.

Usage:
    # Test script step
    python test_client.py script

    # Test HTTP request step
    python test_client.py http

    # Test with custom inputs
    python test_client.py script --code "print('Custom code')"
"""

import asyncio
import base64
import json
import sys
from typing import Any

import grpc


async def call_health_check(host: str, port: int) -> dict[str, Any]:
    """Call the Health RPC to check if container is ready.

    Args:
        host: Container host
        port: Container port

    Returns:
        Health response dict

    """
    # Import proto definitions
    sys.path.insert(0, "../syntara-plugin-sdk/sdks/python/packages/runtime/src")
    from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

    channel = grpc.aio.insecure_channel(f"{host}:{port}")
    stub = container_pb2_grpc.ContainerServiceStub(channel)

    try:
        request = container_pb2.HealthRequest()
        response = await stub.Health(request)

        result = {
            "ready": response.ready,
            "runtime_abi": response.runtime_abi,
            "step_revision_ids": list(response.step_revision_ids),
        }

        print(f"Health check: {result}")
        return result

    finally:
        await channel.close()


async def call_script_step(
    host: str = "localhost",
    port: int = 50051,
    language: str = "python",
    code: str = 'print("Hello from container!")',
    environment: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """Call the script step container.

    Args:
        host: Container host
        port: Container port
        language: Script language (python or bash)
        code: Script code to execute
        environment: Environment variables
        timeout: Timeout in seconds

    Returns:
        Script execution result

    """
    # Import proto definitions
    sys.path.insert(0, "../syntara-plugin-sdk/sdks/python/packages/runtime/src")
    from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

    # Prepare inputs
    inputs = {
        "language": language,
        "code": code,
        "environment": environment or {},
    }

    inputs_json = json.dumps(inputs).encode("utf-8")
    runtime_context_json = json.dumps({}).encode("utf-8")
    credential_file_paths_json = json.dumps({}).encode("utf-8")

    # Create request
    request = container_pb2.ExecuteRequest(
        invocation_id="test-script-001",
        workflow_run_id="test-wf-001",
        step_revision_id="syntara.step.script:0.1.0",
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=inputs_json,
        runtime_context_json=runtime_context_json,
        credential_file_paths_json=credential_file_paths_json,
        timeout_seconds=timeout,
    )

    channel = grpc.aio.insecure_channel(f"{host}:{port}")
    stub = container_pb2_grpc.ContainerServiceStub(channel)

    try:
        print(f"\nCalling script container at {host}:{port}")
        print(f"  Language: {language}")
        print(f"  Code: {code[:100]}...")

        # Call Execute RPC (returns a stream)
        stream = stub.Execute(request, timeout=timeout + 5)

        async for event in stream:
            if event.HasField("progress"):
                progress = event.progress
                print(f"📊 Progress: {progress.message}")

            elif event.HasField("result"):
                result = event.result
                # Proto bytes field gives us raw bytes, not base64
                output_json = result.output_json.decode("utf-8")
                output = json.loads(output_json)

                print("Script executed successfully")
                print(f"  Return Code: {output.get('return_code')}")
                print(f"  Stdout: {output.get('stdout')}")
                print(f"  Stderr: {output.get('stderr')}")
                if output.get("stdout_json"):
                    print(f"  Stdout JSON: {output.get('stdout_json')}")

                return output

            elif event.HasField("failure"):
                failure = event.failure
                print(f"Script failed:")
                print(f"  Code: {failure.code}")
                print(f"  Message: {failure.message}")
                print(f"  Retryable: {failure.retryable}")

                raise RuntimeError(f"Script execution failed: {failure.message}")

        raise RuntimeError("No result or failure received from container")

    finally:
        await channel.close()


async def call_http_step(
    host: str = "localhost",
    port: int = 50051,
    url: str = "https://httpbin.org/get",
    method: str = "GET",
    headers: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Call the HTTP request step container.

    Args:
        host: Container host
        port: Container port
        url: Request URL
        method: HTTP method
        headers: Request headers
        query_params: Query parameters
        body: Request body
        timeout: Timeout in seconds

    Returns:
        HTTP response result

    """
    # Import proto definitions
    sys.path.insert(0, "../syntara-plugin-sdk/sdks/python/packages/runtime/src")
    from syntara_plugin.runtime.protocol import container_pb2, container_pb2_grpc

    # Prepare inputs
    inputs = {
        "url": url,
        "method": method,
        "headers": headers or {},
        "query_params": query_params or {},
        "body": body,
    }

    inputs_json = json.dumps(inputs).encode("utf-8")
    runtime_context_json = json.dumps({}).encode("utf-8")
    credential_file_paths_json = json.dumps({}).encode("utf-8")

    # Create request
    request = container_pb2.ExecuteRequest(
        invocation_id="test-http-001",
        workflow_run_id="test-wf-001",
        step_revision_id="syntara.step.http-request:0.1.0",
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=inputs_json,
        runtime_context_json=runtime_context_json,
        credential_file_paths_json=credential_file_paths_json,
        timeout_seconds=timeout,
    )

    channel = grpc.aio.insecure_channel(f"{host}:{port}")
    stub = container_pb2_grpc.ContainerServiceStub(channel)

    try:
        print(f"\nCalling HTTP request container at {host}:{port}")
        print(f"  Method: {method}")
        print(f"  URL: {url}")

        # Call Execute RPC (returns a stream)
        stream = stub.Execute(request, timeout=timeout + 5)

        async for event in stream:
            if event.HasField("progress"):
                progress = event.progress
                print(f"Progress: {progress.message}")

            elif event.HasField("result"):
                result = event.result
                # Proto bytes field gives us raw bytes, not base64
                output_json = result.output_json.decode("utf-8")
                output = json.loads(output_json)

                print("HTTP request completed successfully")
                print(f"  Status Code: {output.get('status_code')}")
                print(f"  Elapsed: {output.get('elapsed'):.2f}s")
                print(f"  Body: {json.dumps(output.get('body'), indent=2)[:500]}...")

                return output

            elif event.HasField("failure"):
                failure = event.failure
                print(f"HTTP request failed:")
                print(f"  Code: {failure.code}")
                print(f"  Message: {failure.message}")
                print(f"  Retryable: {failure.retryable}")

                raise RuntimeError(f"HTTP request failed: {failure.message}")

        raise RuntimeError("No result or failure received from container")

    finally:
        await channel.close()


async def test_script_examples():
    """Run example script executions."""
    print("\nTesting Script Step Container")
    print("-" * 50)

    # Health check
    await call_health_check("localhost", 50051)

    # Example 1: Simple Python print
    print("\nExample 1: Python Print")
    await call_script_step(
        language="python",
        code='print("Hello from container")',
    )

    # Example 2: Bash echo
    print("\nExample 2: Bash Echo")
    await call_script_step(
        language="bash",
        code='echo "Hello from bash"',
    )

    # Example 3: Python with environment variable
    print("\nExample 3: Environment Variables")
    await call_script_step(
        language="python",
        code='import os; print(f"API_KEY={os.environ.get(\'API_KEY\', \'not set\')}")',
        environment={"API_KEY": "test-key-123"},
    )

    # Example 4: Python with JSON output
    print("\nExample 4: JSON Output")
    await call_script_step(
        language="python",
        code='import json; print(json.dumps({"status": "success", "value": 42}))',
    )


async def test_http_examples():
    """Run example HTTP request executions."""
    print("\nTesting HTTP Request Step Container")
    print("-" * 50)

    # Health check (HTTP container is on port 50052)
    await call_health_check("localhost", 50052)

    # Example 1: Simple GET request
    print("\n\n--- Example 1: Simple GET Request ---")
    await call_http_step(
        host="localhost",
        port=50052,
        url="https://httpbin.org/get",
        method="GET",
    )

    # Example 2: GET with query params
    print("\n\n--- Example 2: GET with Query Parameters ---")
    await call_http_step(
        host="localhost",
        port=50052,
        url="https://httpbin.org/get",
        method="GET",
        query_params={"key1": "value1", "key2": "value2"},
    )

    # Example 3: POST with JSON body
    print("\n\n--- Example 3: POST with JSON Body ---")
    await call_http_step(
        host="localhost",
        port=50052,
        url="https://httpbin.org/post",
        method="POST",
        headers={"Content-Type": "application/json"},
        body={"message": "Hello from container!", "timestamp": "2024-01-01"},
    )


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python test_client.py [script|http]")
        print()
        print("Examples:")
        print("  python test_client.py script")
        print("  python test_client.py http")
        sys.exit(1)

    step_type = sys.argv[1]

    if step_type == "script":
        await test_script_examples()
    elif step_type == "http":
        await test_http_examples()
    else:
        print(f"Unknown step type: {step_type}")
        print("Valid options: script, http")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
