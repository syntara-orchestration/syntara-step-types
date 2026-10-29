"""Tests for ScriptStep using the syntara-plugin-runtime SDK."""

import pytest
from syntara_node_script import ScriptInput, ScriptStep
from syntara_plugin.runtime import ContainerInvocationError, ExecutionContext, FailureCode


@pytest.fixture
def step() -> ScriptStep:
    return ScriptStep()


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(invocation_id="test-invocation-1")


def test_python_print(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(ScriptInput(language="python", code='print("hello")'), context)
    assert output.return_code == 0
    assert output.stdout == "hello\n"


def test_bash_printf(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(ScriptInput(language="bash", code="printf hello"), context)
    assert output.return_code == 0
    assert output.stdout == "hello"


def test_python_json_output(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(
        ScriptInput(language="python", code='import json; print(json.dumps({"x": 1}))'),
        context,
    )
    assert output.return_code == 0
    assert output.stdout_json == {"x": 1}


def test_nonzero_exit_raises(step: ScriptStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(ScriptInput(language="bash", code="exit 3"), context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED
    assert not exc_info.value.retryable


def test_environment_variables(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(
        ScriptInput(language="bash", code="printf $HELLO", environment={"HELLO": "world"}),
        context,
    )
    assert output.stdout == "world"


def test_python_stderr(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(
        ScriptInput(language="python", code='import sys; sys.stderr.write("err\\n")'),
        context,
    )
    assert output.return_code == 0
    assert "err" in output.stderr


def test_bash_stderr_on_error(step: ScriptStep, context: ExecutionContext) -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        step.run(ScriptInput(language="bash", code="echo oops >&2; exit 1"), context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED
    assert "1" in exc_info.value.safe_message


def test_python_integer_json_precision(step: ScriptStep, context: ExecutionContext) -> None:
    output = step.run(
        ScriptInput(language="python", code='print(\'{"id":1152921504606846977,"empty":null}\')'),
        context,
    )
    assert output.stdout_json == {"id": 1152921504606846977, "empty": None}


@pytest.mark.parametrize("limit", [0, 1_048_577, 1_073_741_824])
def test_capture_limits_rejected_by_model_and_installed_manifest(limit, context):
    from pathlib import Path
    from unittest.mock import patch

    import yaml
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from syntara_node_script.__main__ import STEP_IDENTITY, create_bindings
    from syntara_plugin.runtime import StepExecutor

    raw = {"language": "python", "code": 'print("unused")', "max_output_bytes": limit}
    with pytest.raises(ValidationError):
        ScriptInput(**raw)
    manifest_path = Path(__file__).resolve().parents[1] / "manifest.yaml"
    schema = yaml.safe_load(manifest_path.read_text())["spec"]["input"]["value"]
    assert list(Draft202012Validator(schema).iter_errors(raw))
    binding = create_bindings(manifest_path).binding(STEP_IDENTITY)
    with patch("asyncio.create_subprocess_exec") as spawn:
        result = StepExecutor(binding).execute(raw, context)
    assert result.error.code == "INVALID_INPUT"
    spawn.assert_not_called()


@pytest.mark.parametrize("limit", [1, 1_048_576])
def test_capture_boundaries_are_accepted(limit, context):
    from pathlib import Path

    from syntara_node_script.__main__ import STEP_IDENTITY, create_bindings
    from syntara_plugin.runtime import StepExecutor

    raw = {"language": "bash", "code": "printf x", "max_output_bytes": limit}
    assert ScriptInput(**raw).max_output_bytes == limit
    manifest_path = Path(__file__).resolve().parents[1] / "manifest.yaml"
    binding = create_bindings(manifest_path).binding(STEP_IDENTITY)
    result = StepExecutor(binding).execute(raw, context)
    assert result.status == 0
    assert result.output["stdout"] == "x"


@pytest.mark.parametrize("limit", [0, 1_048_577])
async def test_direct_executor_cannot_bypass_capture_ceiling(limit):
    import sys
    from unittest.mock import AsyncMock, patch

    from syntara_node_script.executor import _execute_script_common

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
        with pytest.raises(ValueError, match="max_output_bytes must be between"):
            await _execute_script_common([sys.executable, "-c", "print(1)"], max_output_bytes=limit)
    spawn.assert_not_called()


async def test_output_is_truncated_at_server_capture_ceiling():
    import sys

    from syntara_node_script.executor import MAX_CAPTURE_BYTES, _execute_script_common

    result = await _execute_script_common(
        [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {MAX_CAPTURE_BYTES + 65536})"],
        max_output_bytes=MAX_CAPTURE_BYTES,
        timeout_seconds=5,
    )
    assert len(result["stdout"].encode()) == MAX_CAPTURE_BYTES
    assert "Output truncated" in result["stderr"]


async def test_serialized_result_limit_is_still_enforced():
    from syntara_node_script.executor import (
        MAX_CAPTURE_BYTES,
        ScriptExecutionError,
        run_script_async,
    )

    with pytest.raises(ScriptExecutionError, match="Script output too large"):
        await run_script_async(
            "python", f"print('x' * {MAX_CAPTURE_BYTES})", {}, max_output_bytes=MAX_CAPTURE_BYTES
        )
