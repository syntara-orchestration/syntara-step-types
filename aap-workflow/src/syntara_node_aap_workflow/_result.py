"""Result projection helpers for the AAP Workflow step.

- ``workflow_job_url`` provenance (plan Step 3): the in-process adapter derives
  it from the integration base URL and the job id via ``build_aap_job_url``
  (``{base_url}/execution/jobs/workflow/{job_id}/output``); it is not taken
  from the AAP response. The container derives it the same way.
- Result size bounding (plan Step 3): the SDK permits 1 MiB for the entire
  serialized result. ``artifacts`` is truncated first — largest keys dropped,
  with a documented marker listing what was removed — and the workflow
  identity fields (``workflow_job_id`` etc.) are never dropped to make room.
"""

from __future__ import annotations

import json
from typing import Any

from syntara_plugin.runtime import ContainerInvocationError, FailureCode

from ._models import AapWorkflowOutput

# SDK container ABI limit for the serialized business result.
MAX_RESULT_BYTES = 1_048_576

# Marker key added to ``artifacts`` when artifact keys were dropped to fit the
# result size limit. The value lists the dropped keys, largest first.
TRUNCATION_MARKER_KEY = "__syntara_truncated_artifacts__"


def build_workflow_job_url(base_url: str, job_id: int) -> str:
    """Build the AAP UI URL for a workflow job, matching the adapter."""
    return f"{base_url.rstrip('/')}/execution/jobs/workflow/{job_id}/output"


def _serialized_size(output: AapWorkflowOutput) -> int:
    return len(json.dumps(output.model_dump(mode="json"), separators=(",", ":")))


def _largest_artifact_key(artifacts: dict[str, Any]) -> str | None:
    """Return the artifact key with the largest serialized footprint."""
    candidates: dict[str, int] = {
        key: len(json.dumps(value, separators=(",", ":")))
        for key, value in artifacts.items()
        if key != TRUNCATION_MARKER_KEY
    }
    if not candidates:
        return None
    return max(candidates, key=lambda key: candidates[key])


def bound_result_size(output: AapWorkflowOutput) -> AapWorkflowOutput:
    """Bound the serialized result to the SDK 1 MiB limit.

    Drops the largest artifact keys (recording them under
    ``__syntara_truncated_artifacts__``) until the result fits. Identity fields
    are never dropped. If the result still exceeds the limit with no artifacts
    left, the failure is deliberate and terminal.
    """
    dropped: list[str] = []
    while _serialized_size(output) > MAX_RESULT_BYTES:
        key = _largest_artifact_key(output.artifacts)
        if key is None:
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                f"Workflow result exceeds the {MAX_RESULT_BYTES} byte limit "
                "even without artifacts.",
                retryable=False,
                outcome="not_completed",
            )
        del output.artifacts[key]
        dropped.append(key)
        output.artifacts[TRUNCATION_MARKER_KEY] = dropped
    return output
