"""Dispatch compiled plugin targets through the shipped image gRPC services."""

from __future__ import annotations

import json
from typing import Any

import pytest
from syntara_plugin.contracts import canonical_json_digest
from syntara_plugin.runtime.protocol import container_pb2
from syntara_plugin.sdk import BuildRequest, compile_workspace

from tests.integration.helpers import execute_events
from tests.integration.service_types import ImageServices
from tests.support.step_type_artifacts import ROOT, load_inventory

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("step", "inputs", "expected"),
    [
        (
            "script-python",
            {"code": 'print("dispatched")'},
            {"stdout": "dispatched\n"},
        ),
        (
            "script-bash",
            {"code": "printf dispatched"},
            {"stdout": "dispatched"},
        ),
        ("http-request", {}, {"body": {"args": {}}}),
    ],
)
def test_execute_using_compiled_plugin_target(
    step: str,
    inputs: dict[str, Any],
    expected: dict[str, Any],
    image_services: ImageServices,
) -> None:
    inventory = load_inventory()
    entry = inventory["plugins"][step]
    workload_id = entry["workloadId"]
    compiled = compile_workspace(
        BuildRequest(
            root_manifest=ROOT / step / "plugin.yaml",
            image_bindings={f"{workload_id}-runner": entry["imageReference"]},
        )
    )
    target = compiled.descriptor["spec"]["targets"][0]
    if step == "http-request":
        inputs = {"url": f"{image_services.upstream_url}/get"}

    output_digest = canonical_json_digest(target["output"])
    request = container_pb2.ExecuteRequest(
        invocation_id=f"compiled-{step}",
        workflow_run_id="wf-compiled-target",
        step_revision_id=target["identity"],
        runtime_abi="syntara.container/v1alpha1",
        inputs_json=json.dumps(inputs).encode(),
        runtime_context_json=json.dumps({"outputSchemaDigest": output_digest}).encode(),
        timeout_seconds=10,
    )
    stub = {
        "http-request": image_services.http,
        "script-python": image_services.python,
        "script-bash": image_services.bash,
    }[step]
    assert (
        target["identity"]
        in stub.Health(container_pb2.HealthRequest(), timeout=2).step_revision_ids
    )
    events = list(execute_events(stub, request, timeout=12))
    assert len(events) == 1
    assert events[0].HasField("result"), events[0]
    assert events[0].result.output_schema_digest == output_digest
    output = json.loads(events[0].result.output_json)
    for key, value in expected.items():
        assert output[key] == value
