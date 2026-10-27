"""Generated local tests for the syntara.script-step.bash Action Step type.

Regenerate with: syntara-plugin test-init plugin.yaml --force
Review the TODO markers and extend the cases with business-specific payloads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from syntara_plugin.testing import (
    assert_outcomes_equivalent,
    invoke_step,
    invoke_step_in_container,
)

ENTRY_POINT = "script_step.runtime:BashScriptStep"
WORKSPACE = (Path(__file__).resolve().parent / '..').resolve()

VALID_CASES = [
    {'code': 'echo hello-from-bash'},
    {'code': 'echo "$POC_GREETING"', 'environment': {'POC_GREETING': 'from-env'}},
]

INVALID_CASES = [
    {},
    {'code': 12345},
]


def test_valid_input_reaches_the_hook_once(mock_execution_context, step_bindings):
    binding = step_bindings.bind_step(ENTRY_POINT, workspace=WORKSPACE)
    outcome = invoke_step(binding, VALID_CASES[0], context=mock_execution_context())
    assert outcome.ok, f"valid case failed (status={outcome.status})"
    assert outcome.run_calls == 1


def test_invalid_input_blocks_the_hook(mock_execution_context, step_bindings):
    binding = step_bindings.bind_step(ENTRY_POINT, workspace=WORKSPACE)
    for inputs in INVALID_CASES:
        outcome = invoke_step(binding, inputs, context=mock_execution_context())
        assert not outcome.ok, "an invalid case was accepted"
        assert outcome.run_calls == 0, "the business hook ran for an invalid case"


def test_direct_and_container_paths_are_equivalent(mock_execution_context, step_bindings):
    binding = step_bindings.bind_step(ENTRY_POINT, workspace=WORKSPACE)
    context = mock_execution_context(invocation_id="generated-equivalence")
    direct = invoke_step(binding, VALID_CASES[0], context=context)
    container = invoke_step_in_container(binding, VALID_CASES[0], context=context)
    assert_outcomes_equivalent(direct, container)


def test_environment_variables_reach_the_script(mock_execution_context, step_bindings):
    binding = step_bindings.bind_step(ENTRY_POINT, workspace=WORKSPACE)
    outcome = invoke_step(binding, VALID_CASES[1], context=mock_execution_context())
    assert outcome.ok, f"valid case failed (status={outcome.status})"
    assert outcome.output["stdout"] == "from-env\n"
