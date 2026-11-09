"""Dispatch e2e: run plugin example workloads through AO and the Execution Plane.

Each test creates a workflow whose script nodes carry the plugin's example
payload, runs it, and asserts the business result — the same predicates as
``make sdk-plugin-run`` — plus ``retry_count == 0`` (a timing-independent
proof that the dispatch succeeded on the first attempt).

Requires a live deployment: ``AO_API_BASE`` must point at the AO API of an
aap-dev environment with ``AO_EXECUTION_PLANE=true`` and the script-node
image built from this checkout (see tools/konflux-run-e2e.sh and the
aap-dev staging hooks).
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e.ao_client import AoApi, script_workflow

pytestmark = [pytest.mark.e2e]

POLL_TIMEOUT_SECONDS = 300


def _activities(execution: dict) -> dict[str, dict]:
    return {activity["activity_id"]: activity for activity in execution.get("activities", [])}


def _output_data(activity: dict) -> dict:
    output = activity.get("output_data") or {}
    if isinstance(output, dict):
        return output
    return {}


def test_script_python_example_dispatches_and_completes(ao_api: AoApi, e2e_project_id) -> None:
    """script-python: the example workload returns {'result': 42} as JSON stdout."""

    workflow_id = ao_api.create_workflow(
        f"e2e-script-python-{uuid.uuid4().hex[:8]}",
        script_workflow(
            "script_python_e2e",
            [
                {
                    "id": "compute",
                    "name": "Compute",
                    "type": "script",
                    "parameters": {
                        "language": "python",
                        "code": "import json\nprint(json.dumps({'result': 42}))",
                    },
                }
            ],
        ),
        e2e_project_id,
    )
    execution = ao_api.wait_for_terminal(
        ao_api.create_execution(workflow_id), timeout_seconds=POLL_TIMEOUT_SECONDS
    )

    assert execution["status"] == "completed", f"execution: {execution}"
    activity = _activities(execution)["compute"]
    output = _output_data(activity)
    assert output.get("return_code") == 0, f"activity output: {output}"
    stdout_json = output.get("stdout_json") or {}
    assert stdout_json.get("result") == 42, f"activity output: {output}"
    assert activity.get("retry_count", 0) == 0, f"activity retried: {activity}"


def test_script_bash_example_dispatches_and_completes(ao_api: AoApi, e2e_project_id) -> None:
    """script-bash: the example workload prints 'hello from bash'."""

    workflow_id = ao_api.create_workflow(
        f"e2e-script-bash-{uuid.uuid4().hex[:8]}",
        script_workflow(
            "script_bash_e2e",
            [
                {
                    "id": "greet",
                    "name": "Greet",
                    "type": "script",
                    "parameters": {"language": "bash", "code": "echo hello from bash"},
                }
            ],
        ),
        e2e_project_id,
    )
    execution = ao_api.wait_for_terminal(
        ao_api.create_execution(workflow_id), timeout_seconds=POLL_TIMEOUT_SECONDS
    )

    assert execution["status"] == "completed", f"execution: {execution}"
    activity = _activities(execution)["greet"]
    output = _output_data(activity)
    assert output.get("return_code") == 0, f"activity output: {output}"
    assert output.get("stdout") == "hello from bash\n", f"activity output: {output}"
    assert activity.get("retry_count", 0) == 0, f"activity retried: {activity}"


def test_script_python_chain_second_failure_fails_execution(ao_api: AoApi, e2e_project_id) -> None:
    """A failing script node fails the execution and surfaces its return code."""

    workflow_id = ao_api.create_workflow(
        f"e2e-script-fail-{uuid.uuid4().hex[:8]}",
        script_workflow(
            "script_fail_e2e",
            [
                {
                    "id": "ok",
                    "name": "OK",
                    "type": "script",
                    "parameters": {"language": "python", "code": "print('fine')"},
                },
                {
                    "id": "boom",
                    "name": "Boom",
                    "type": "script",
                    "parameters": {"language": "python", "code": "raise SystemExit(3)"},
                    "settings": {"continue_on_failure": True},
                },
            ],
        ),
        e2e_project_id,
    )
    execution = ao_api.wait_for_terminal(
        ao_api.create_execution(workflow_id), timeout_seconds=POLL_TIMEOUT_SECONDS
    )

    assert execution["status"] == "failed", f"execution: {execution}"
    boom = _activities(execution)["boom"]
    output = _output_data(boom)
    assert output.get("return_code") == 3, f"activity output: {output}"


def _ep_result(item: dict) -> dict:
    result = item.get("result") or {}
    if isinstance(result, dict):
        return result
    return {}


@pytest.mark.skipif(
    not os.environ.get("EP_API_URL") or not os.environ.get("EP_TOOLS_DIR"),
    reason="direct Execution Plane dispatch needs EP_API_URL and EP_TOOLS_DIR",
)
def test_script_python_plugin_dispatches_via_execution_plane() -> None:
    """script-python plugin image, direct dispatch: example workload returns 42."""

    from tests.e2e.ep_direct import dispatch_plugin_workload

    image = os.environ["SYNTARA_NODE_SCRIPT_PYTHON_IMAGE"]
    item = dispatch_plugin_workload(
        step_revision_id="syntara.script-python.script-python",
        image=image,
        inputs={"code": "import json\nprint(json.dumps({'result': 42}))"},
    )
    assert item.get("status") == "completed", f"work item: {item}"
    output = _ep_result(item).get("output") or {}
    assert output.get("return_code") == 0, f"output: {output}"
    stdout_json = output.get("stdout_json") or {}
    assert stdout_json.get("result") == 42, f"output: {output}"
    assert item.get("retry_count", 0) == 0, f"work item retried: {item}"


@pytest.mark.skipif(
    not os.environ.get("EP_API_URL") or not os.environ.get("EP_TOOLS_DIR"),
    reason="direct Execution Plane dispatch needs EP_API_URL and EP_TOOLS_DIR",
)
def test_script_bash_plugin_dispatches_via_execution_plane() -> None:
    """script-bash plugin image, direct dispatch: example workload prints its greeting."""

    from tests.e2e.ep_direct import dispatch_plugin_workload

    image = os.environ["SYNTARA_NODE_SCRIPT_BASH_IMAGE"]
    item = dispatch_plugin_workload(
        step_revision_id="syntara.script-bash.script-bash",
        image=image,
        inputs={"code": "printf hello from bash"},
    )
    assert item.get("status") == "completed", f"work item: {item}"
    output = _ep_result(item).get("output") or {}
    assert output.get("return_code") == 0, f"output: {output}"
    assert output.get("stdout") == "hello from bash", f"output: {output}"
    assert item.get("retry_count", 0) == 0, f"work item retried: {item}"
