"""Tests for the Python and Bash script plugin steps."""

import pytest
from syntara_node_script import BashScriptStep, PythonScriptStep, ScriptInput
from syntara_plugin.runtime import ExecutionContext, FailureCode, StepFailure


@pytest.fixture
def python_step() -> PythonScriptStep:
    return PythonScriptStep()


@pytest.fixture
def bash_step() -> BashScriptStep:
    return BashScriptStep()


@pytest.fixture
def context() -> ExecutionContext:
    return ExecutionContext(invocation_id="test-invocation-1")


def test_python_print(python_step: PythonScriptStep, context: ExecutionContext) -> None:
    output = python_step.run(ScriptInput(code='print("hello")'), context)
    assert output.return_code == 0
    assert output.stdout == "hello\n"


def test_bash_printf(bash_step: BashScriptStep, context: ExecutionContext) -> None:
    output = bash_step.run(ScriptInput(code="printf hello"), context)
    assert output.return_code == 0
    assert output.stdout == "hello"
    assert "stdout_json" not in output.model_dump()


def test_python_json_output(python_step: PythonScriptStep, context: ExecutionContext) -> None:
    output = python_step.run(
        ScriptInput(code='import json; print(json.dumps({"x": 1}))'),
        context,
    )
    assert output.return_code == 0
    assert output.stdout_json == {"x": 1}


def test_nonzero_exit_raises(bash_step: BashScriptStep, context: ExecutionContext) -> None:
    inputs = ScriptInput(code="exit 3")
    with pytest.raises(StepFailure) as exc_info:
        bash_step.run(inputs, context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED.value
    assert not exc_info.value.retryable


def test_environment_variables(bash_step: BashScriptStep, context: ExecutionContext) -> None:
    output = bash_step.run(
        ScriptInput(code="printf $HELLO", environment={"HELLO": "world"}),
        context,
    )
    assert output.stdout == "world"


def test_python_stderr(python_step: PythonScriptStep, context: ExecutionContext) -> None:
    output = python_step.run(
        ScriptInput(code='import sys; sys.stderr.write("err\\n")'),
        context,
    )
    assert output.return_code == 0
    assert "err" in output.stderr


def test_bash_stderr_on_error(bash_step: BashScriptStep, context: ExecutionContext) -> None:
    inputs = ScriptInput(code="echo oops >&2; exit 1")
    with pytest.raises(StepFailure) as exc_info:
        bash_step.run(inputs, context)
    assert exc_info.value.code == FailureCode.REMOTE_REJECTED.value
    assert "1" in exc_info.value.error_message


def test_python_integer_json_precision(
    python_step: PythonScriptStep, context: ExecutionContext
) -> None:
    output = python_step.run(
        ScriptInput(code='print(\'{"id":1152921504606846977,"empty":null}\')'),
        context,
    )
    assert output.stdout_json == {"id": 1152921504606846977, "empty": None}


def test_script_language_is_fixed_by_plugin() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ScriptInput(code="printf hello", language="bash")


@pytest.mark.parametrize("limit", [0, 1_048_577, 1_073_741_824])
def test_capture_limits_rejected_by_model_and_installed_manifest(limit, context):
    from pathlib import Path
    from unittest.mock import patch

    import yaml
    from jsonschema import Draft202012Validator
    from pydantic import ValidationError
    from syntara_node_script.python import STEP_IDENTITY, create_bindings
    from syntara_plugin.runtime import StepExecutor

    raw = {"code": 'print("unused")', "max_output_bytes": limit}
    with pytest.raises(ValidationError):
        ScriptInput(**raw)
    manifest_path = Path(__file__).resolve().parents[2] / "script-python" / "manifest.yaml"
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

    from syntara_node_script.bash import STEP_IDENTITY, create_bindings
    from syntara_plugin.runtime import StepExecutor

    raw = {"code": "printf x", "max_output_bytes": limit}
    assert ScriptInput(**raw).max_output_bytes == limit
    manifest_path = Path(__file__).resolve().parents[2] / "script-bash" / "manifest.yaml"
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
