"""Tests for ScriptStep using the syntara-plugin-runtime SDK."""
import pytest
from syntara_plugin.runtime import ContainerInvocationError, ExecutionContext, FailureCode

from syntara_node_script import ScriptInput, ScriptStep


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
