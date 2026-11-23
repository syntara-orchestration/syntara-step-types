"""Executor tests for the AAP Workflow step (plan Step 4).

Uses respx to fake the AAP gateway API and covers the failure semantics
recorded in the plan: pre-send vs post-send loss, launch rejection mapping,
parent-only polling, transient poll retries, timeout vs lost contact, and
cancellation with a best-effort AAP cancel.
"""

from __future__ import annotations

from threading import Event

import httpx
import pytest
import respx
from syntara_node_aap_workflow import executor
from syntara_node_aap_workflow._auth import AapResolvedAuth
from syntara_node_aap_workflow._models import AapWorkflowInput
from syntara_node_aap_workflow.executor import execute_aap_workflow
from syntara_plugin.runtime import ContainerInvocationError, FailureCode

BASE = "https://aap.example.com"
PREFIX = f"{BASE}{executor.AAP_API_PREFIX}"

CONNECTION = AapResolvedAuth(
    base_url=BASE,
    auth_headers={"Authorization": "Bearer tok"},
    basic_auth=None,
    verify_ssl=True,
    ca_certificate=None,
)


def _fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(executor, "RESOLUTION_BACKOFF_SECONDS", 0.01)


def _job(status: str, **extra: object) -> dict:
    return {
        "id": 7,
        "status": status,
        "artifacts": {"result": "done"},
        "created": "2026-01-01T00:00:00Z",
        "started": "2026-01-01T00:00:01Z",
        "finished": "2026-01-01T00:00:02Z",
        **extra,
    }


def _launch_route(**kwargs: object) -> object:
    return respx.post(f"{PREFIX}/workflow_job_templates/42/launch/").mock(**kwargs)


def _status_route(**kwargs: object) -> object:
    return respx.get(f"{PREFIX}/workflow_jobs/7/").mock(**kwargs)


@respx.mock
def test_successful_launch_and_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))

    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)

    assert output.status == "completed"
    assert output.workflow_job_id == 7
    assert output.workflow_job_status == "successful"
    assert output.workflow_job_url == f"{BASE}/execution/jobs/workflow/7/output"
    assert output.artifacts == {"result": "done"}
    assert output.created == "2026-01-01T00:00:00Z"


@respx.mock
def test_launch_body_maps_fields_like_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    route = _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))

    execute_aap_workflow(
        AapWorkflowInput(
            workflow_job_template_id=42,
            extra_vars={"env": "prod"},
            limit="host1",
            scm_branch="devel",
            tags="install",
            skip_tags="debug",
            inventory_id=5,
        ),
        CONNECTION,
    )

    import json

    body = json.loads(route.calls[0].request.read())
    # The correlation token is attached under a reserved key (plan Step 5).
    token = body["extra_vars"].pop(executor.LAUNCH_TOKEN_EXTRA_VAR)
    assert len(token) == 32
    assert body == {
        "inventory": 5,
        "extra_vars": {"env": "prod"},
        "limit": "host1",
        "scm_branch": "devel",
        "job_tags": "install",  # tags → job_tags, matching the adapter
        "skip_tags": "debug",
    }


@respx.mock
def test_template_name_lookup_resolves_then_launches(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    respx.get(f"{PREFIX}/workflow_job_templates/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 42}]})
    )
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))

    output = execute_aap_workflow(
        AapWorkflowInput(
            workflow_job_template_name="example-workflow", organization_name="Default"
        ),
        CONNECTION,
    )
    assert output.status == "completed"


@respx.mock
def test_missing_template_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    respx.get(f"{PREFIX}/workflow_job_templates/").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(workflow_job_template_name="nope", organization_name="Default"),
            CONNECTION,
        )
    assert exc_info.value.code == FailureCode.NOT_FOUND
    assert exc_info.value.retryable is False


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (401, FailureCode.AUTHENTICATION_FAILED),
        (403, FailureCode.AUTHORIZATION_FAILED),
        (404, FailureCode.NOT_FOUND),
        (500, FailureCode.REMOTE_REJECTED),  # launch 500 is non-retryable
        (429, FailureCode.REMOTE_REJECTED),  # sent → non-retryable per plan
    ],
)
@respx.mock
def test_launch_rejection_mapping(
    monkeypatch: pytest.MonkeyPatch, status_code: int, expected_code: FailureCode
) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(status_code, json={}))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is False


@respx.mock
def test_connection_loss_before_send_is_retryable_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_polls(monkeypatch)
    _launch_route(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.UPSTREAM_TRANSIENT
    assert exc_info.value.retryable is True


def _jobs_route(**kwargs: object) -> object:
    return respx.get(f"{PREFIX}/workflow_job_templates/42/jobs/").mock(**kwargs)


def _correlated_job(token_value: str, status: str = "running") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {"id": 7, "status": status, "extra_vars": {"_syntara_launch_token": token_value}}
            ]
        },
    )


@respx.mock
def test_lost_launch_response_is_outcome_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A POST that may have been sent is never blindly retried — uncertain launch."""
    _fast_polls(monkeypatch)
    launch = _launch_route(side_effect=httpx.ReadTimeout("lost"))
    _jobs_route(return_value=httpx.Response(200, json={"results": []}))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.OUTCOME_UNKNOWN
    assert exc_info.value.retryable is False
    # Two lost responses → two lookups, but at most two launch POSTs total.
    assert launch.call_count == executor.MAX_LAUNCH_ATTEMPTS


@respx.mock
def test_launch_body_carries_correlation_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    route = _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))
    execute_aap_workflow(
        AapWorkflowInput(workflow_job_template_id=42, extra_vars={"env": "prod"}), CONNECTION
    )
    import json

    extra_vars = json.loads(route.calls[0].request.read())["extra_vars"]
    assert extra_vars["env"] == "prod"  # user extra_vars preserved
    assert executor.LAUNCH_TOKEN_EXTRA_VAR in extra_vars  # token attached
    assert len(extra_vars[executor.LAUNCH_TOKEN_EXTRA_VAR]) == 32  # uuid4 hex


@respx.mock
def test_reserved_extra_var_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(
                workflow_job_template_id=42,
                extra_vars={executor.LAUNCH_TOKEN_EXTRA_VAR: "spoof"},
            ),
            CONNECTION,
        )
    assert exc_info.value.code == FailureCode.INVALID_INPUT


@respx.mock
def test_lost_launch_adopts_correlated_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lost response reconciles via the token and never re-POSTs."""
    _fast_polls(monkeypatch)

    token_holder: dict[str, str] = {}

    def _launch_side_effect(request: httpx.Request) -> httpx.Response:
        import json

        token_holder["token"] = json.loads(request.read())["extra_vars"][
            executor.LAUNCH_TOKEN_EXTRA_VAR
        ]
        raise httpx.ReadTimeout("lost")

    launch = _launch_route(side_effect=_launch_side_effect)

    def _jobs_side_effect(request: httpx.Request) -> httpx.Response:
        return _correlated_job(token_holder.get("token", ""))

    _jobs_route(side_effect=_jobs_side_effect)
    _status_route(return_value=httpx.Response(200, json=_job("successful")))

    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)

    assert output.status == "completed"
    assert output.workflow_job_id == 7
    assert launch.call_count == 1  # adopted; no second POST


@respx.mock
def test_adoption_matches_pre_terminal_states(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    token_holder: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        token_holder["token"] = json.loads(request.read())["extra_vars"][
            executor.LAUNCH_TOKEN_EXTRA_VAR
        ]
        raise httpx.ReadTimeout("lost")

    _launch_route(side_effect=_capture)

    def _pending_job(request: httpx.Request) -> httpx.Response:
        return _correlated_job(token_holder.get("token", ""), status="pending")

    _jobs_route(side_effect=_pending_job)
    _status_route(return_value=httpx.Response(200, json=_job("successful")))
    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert output.status == "completed"


@respx.mock
def test_adoption_ignores_terminal_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token match that already finished is not adopted — keep looking."""
    _fast_polls(monkeypatch)
    launch = _launch_route(side_effect=httpx.ReadTimeout("lost"))
    _jobs_route(return_value=_correlated_job("any", status="successful"))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.OUTCOME_UNKNOWN
    assert launch.call_count == executor.MAX_LAUNCH_ATTEMPTS


@respx.mock
def test_lost_launch_with_no_correlated_job_launches_once_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_polls(monkeypatch)
    responses = [httpx.ReadTimeout("lost"), httpx.Response(201, json={"id": 7})]
    launch = _launch_route(side_effect=responses)
    _jobs_route(return_value=httpx.Response(200, json={"results": []}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))
    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert output.status == "completed"
    assert launch.call_count == 2  # one further launch allowed after empty lookup


@respx.mock
def test_ambiguous_reconciliation_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_polls(monkeypatch)
    token_holder: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        token_holder["token"] = json.loads(request.read())["extra_vars"][
            executor.LAUNCH_TOKEN_EXTRA_VAR
        ]
        raise httpx.ReadTimeout("lost")

    _launch_route(side_effect=_capture)

    def _two_matches(request: httpx.Request) -> httpx.Response:
        token = token_holder.get("token", "")
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": 7, "status": "running", "extra_vars": {"_syntara_launch_token": token}},
                    {"id": 8, "status": "running", "extra_vars": {"_syntara_launch_token": token}},
                ]
            },
        )

    _jobs_route(side_effect=_two_matches)
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.OUTCOME_UNKNOWN


@respx.mock
def test_failed_reconciliation_lookup_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_polls(monkeypatch)
    _launch_route(side_effect=httpx.ReadTimeout("lost"))
    _jobs_route(side_effect=httpx.ConnectError("lookup lost"))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.OUTCOME_UNKNOWN


@respx.mock
def test_parent_failure_status_returns_failed_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("failed")))

    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)

    assert output.status == "failed"
    assert output.workflow_job_id == 7
    assert output.workflow_job_status == "failed"
    assert output.error is not None and "failed" in output.error


@respx.mock
def test_transparent_poll_errors_retry_then_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(
        side_effect=[
            httpx.Response(500),  # transient during polling
            httpx.Response(503),  # transient during polling
            httpx.Response(200, json=_job("successful")),
        ]
    )
    output = execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert output.status == "completed"


@respx.mock
def test_consecutive_poll_errors_report_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(500))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42), CONNECTION)
    assert exc_info.value.code == FailureCode.OUTCOME_UNKNOWN
    assert exc_info.value.retryable is False


@respx.mock
def test_healthy_polls_past_deadline_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("running", finished=None)))
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(AapWorkflowInput(workflow_job_template_id=42, timeout=1), CONNECTION)
    assert exc_info.value.code == FailureCode.TIMEOUT


@respx.mock
def test_cancellation_requests_aap_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("running", finished=None)))
    cancel_route = respx.post(f"{PREFIX}/workflow_jobs/7/cancel/").mock(
        return_value=httpx.Response(202, json={})
    )

    cancellation = Event()

    def _set_cancel_after_first_poll(request: httpx.Request) -> httpx.Response:
        cancellation.set()
        return httpx.Response(200, json=_job("running", finished=None))

    _status_route(side_effect=_set_cancel_after_first_poll)

    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(workflow_job_template_id=42), CONNECTION, cancellation
        )
    assert exc_info.value.code == FailureCode.CANCELLED
    assert cancel_route.called  # best-effort cancel was requested


@respx.mock
def test_failed_cancel_post_still_reports_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    cancellation = Event()

    def _set_cancel(request: httpx.Request) -> httpx.Response:
        cancellation.set()
        return httpx.Response(200, json=_job("running", finished=None))

    _status_route(side_effect=_set_cancel)
    respx.post(f"{PREFIX}/workflow_jobs/7/cancel/").mock(
        side_effect=httpx.ConnectError("cancel lost")
    )
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(workflow_job_template_id=42), CONNECTION, cancellation
        )
    assert exc_info.value.code == FailureCode.CANCELLED


def test_cancellation_before_start_skips_aap() -> None:
    cancellation = Event()
    cancellation.set()
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(workflow_job_template_id=42), CONNECTION, cancellation
        )
    assert exc_info.value.code == FailureCode.CANCELLED
    assert exc_info.value.safe_message  # user-safe message present


@respx.mock
def test_label_creation_and_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    _fast_polls(monkeypatch)
    respx.get(f"{PREFIX}/organizations/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": 1}]})
    )
    respx.get(f"{PREFIX}/labels/").mock(return_value=httpx.Response(200, json={"results": []}))
    create_label = respx.post(f"{PREFIX}/labels/").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )
    launch = _launch_route(return_value=httpx.Response(201, json={"id": 7}))
    _status_route(return_value=httpx.Response(200, json=_job("successful")))

    output = execute_aap_workflow(
        AapWorkflowInput(workflow_job_template_id=42, labels=["deploy"]), CONNECTION
    )
    assert output.status == "completed"
    assert create_label.called
    import json

    assert json.loads(launch.calls[0].request.read())["labels"] == [99]


@respx.mock
def test_invalid_label_name_rejected_before_any_call() -> None:
    with pytest.raises(ContainerInvocationError) as exc_info:
        execute_aap_workflow(
            AapWorkflowInput(workflow_job_template_id=42, labels=["bad;label"]), CONNECTION
        )
    assert exc_info.value.code == FailureCode.INVALID_INPUT
