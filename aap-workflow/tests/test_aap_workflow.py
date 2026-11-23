"""Unit tests for the AAP Workflow step (scaffold + Step 2 connection resolution)."""

from __future__ import annotations

import json
import logging
from threading import Event, Thread

import httpx
import pytest
import respx
from syntara_node_aap_workflow import AapWorkflowInput, AapWorkflowOutput, AapWorkflowStep
from syntara_node_aap_workflow._auth import (
    AAP_CREDENTIAL_NAME,
    resolve_aap_auth,
    resolve_aap_connection,
    validate_aap_endpoint,
    validate_aap_integration,
)
from syntara_node_aap_workflow._result import (
    MAX_RESULT_BYTES,
    TRUNCATION_MARKER_KEY,
    bound_result_size,
    build_workflow_job_url,
)
from syntara_plugin.runtime import (
    ContainerInvocationError,
    ExecutionContext,
    FailureCode,
    StepFailure,
)
from syntara_plugin.runtime.credential_callback import CallbackCredentialProvider
from syntara_plugin.runtime.protocol import container_pb2

_VALID_RUNTIME_CONTEXT = {
    "aapEndpoint": "https://aap.example.com",
    "aapVerifySsl": True,
}


def _credential_context(document: dict, runtime_context: dict | None = None) -> ExecutionContext:
    """Build an ExecutionContext whose aap-credential resolves to ``document``."""
    provider: CallbackCredentialProvider

    def mock_callback(request: container_pb2.CredentialRequest) -> None:
        response = container_pb2.CredentialResponse(
            invocation_id=request.invocation_id,
            request_id=request.request_id,
            document_json=json.dumps(document).encode(),
        )

        def deliver() -> None:
            provider.receive(response)

        Thread(target=deliver, daemon=True).start()

    provider = CallbackCredentialProvider(
        invocation_id="credential-invocation",
        send=mock_callback,
        cancellation=Event(),
        timeout=5,
    )
    return ExecutionContext(
        invocation_id="credential-invocation",
        runtime_context=runtime_context or _VALID_RUNTIME_CONTEXT,
        credential_provider=provider,
    )._with_credentials((AAP_CREDENTIAL_NAME,))


class TestAapWorkflowInput:
    def test_template_id_alone_is_valid(self) -> None:
        model = AapWorkflowInput(workflow_job_template_id=42)
        assert model.workflow_job_template_id == 42

    def test_template_name_requires_organization(self) -> None:
        with pytest.raises(ValueError, match="organization_name"):
            AapWorkflowInput(workflow_job_template_name="example-workflow")

    def test_template_name_with_organization_is_valid(self) -> None:
        model = AapWorkflowInput(
            workflow_job_template_name="example-workflow", organization_name="Default"
        )
        assert model.organization_name == "Default"

    def test_template_reference_is_required(self) -> None:
        with pytest.raises(ValueError, match="workflow_job_template"):
            AapWorkflowInput()

    def test_inventory_name_requires_organization(self) -> None:
        with pytest.raises(ValueError, match="inventory_name requires organization_name"):
            AapWorkflowInput(
                workflow_job_template_id=42,
                inventory_name="production",
            )

    def test_credential_name_is_not_author_input(self) -> None:
        """Plan Step 2: neither URL nor credential is a free-form step input."""
        with pytest.raises(ValueError):
            AapWorkflowInput(workflow_job_template_id=42, credential_name="nope")  # type: ignore[call-arg]

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValueError):
            AapWorkflowInput(workflow_job_template_id=42, integration_id="nope")  # type: ignore[call-arg]


class TestAapWorkflowOutput:
    """The output preserves the adapter's discriminated union on status."""

    def test_completed_result_requires_identity_and_timestamps(self) -> None:
        with pytest.raises(ValueError, match="completed result requires"):
            AapWorkflowOutput(status="completed", workflow_job_id=7)

    def test_completed_result_is_valid(self) -> None:
        model = AapWorkflowOutput(
            status="completed",
            workflow_job_id=7,
            created="2026-01-01T00:00:00Z",
            started="2026-01-01T00:00:01Z",
            finished="2026-01-01T00:00:02Z",
        )
        assert model.workflow_job_id == 7
        assert model.error is None

    def test_failed_result_requires_error(self) -> None:
        with pytest.raises(ValueError, match="failed result requires error"):
            AapWorkflowOutput(status="failed", workflow_job_id=7)

    def test_failed_result_allows_null_fields(self) -> None:
        model = AapWorkflowOutput(status="failed", error="boom")
        assert model.workflow_job_id is None
        assert model.workflow_job_status is None
        assert model.artifacts == {}


class TestWorkflowJobUrl:
    def test_matches_adapter_formula(self) -> None:
        # build_aap_job_url(base_url, job_id, "workflow") in aap_common.py.
        assert (
            build_workflow_job_url("https://aap.example.com", 7)
            == "https://aap.example.com/execution/jobs/workflow/7/output"
        )

    def test_trailing_slash_base_url(self) -> None:
        assert (
            build_workflow_job_url("https://aap.example.com/", 7)
            == "https://aap.example.com/execution/jobs/workflow/7/output"
        )


class TestBoundResultSize:
    def test_small_result_untouched(self) -> None:
        output = AapWorkflowOutput(
            status="completed",
            workflow_job_id=7,
            created="2026-01-01T00:00:00Z",
            started="2026-01-01T00:00:01Z",
            finished="2026-01-01T00:00:02Z",
            artifacts={"small": "x"},
        )
        bounded = bound_result_size(output)
        assert bounded.artifacts == {"small": "x"}
        assert TRUNCATION_MARKER_KEY not in bounded.artifacts

    def test_truncation_keeps_identity_fields_and_marks_dropped(self) -> None:
        big_a = "a" * 600_000
        big_b = "b" * 600_000
        output = AapWorkflowOutput(
            status="completed",
            workflow_job_id=7,
            created="2026-01-01T00:00:00Z",
            started="2026-01-01T00:00:01Z",
            finished="2026-01-01T00:00:02Z",
            artifacts={"big_a": big_a, "big_b": big_b},
        )
        bounded = bound_result_size(output)
        assert bounded.workflow_job_id == 7
        assert bounded.created is not None
        assert len(json.dumps(bounded.model_dump(mode="json"))) <= MAX_RESULT_BYTES
        marker = bounded.artifacts[TRUNCATION_MARKER_KEY]
        assert isinstance(marker, list)
        assert len(marker) == 1  # only the largest key needed dropping

    def test_result_over_limit_without_artifacts_is_terminal(self) -> None:
        output = AapWorkflowOutput(status="failed", error="x" * 2_000_000)
        with pytest.raises(ContainerInvocationError) as exc_info:
            bound_result_size(output)
        assert exc_info.value.code == FailureCode.POLICY_DENIED
        assert exc_info.value.retryable is False


class TestValidateAapEndpoint:
    def test_valid_endpoint(self) -> None:
        assert validate_aap_endpoint({"aapEndpoint": "https://aap.example.com"}) == (
            "https://aap.example.com"
        )

    @pytest.mark.parametrize(
        "runtime_context",
        [
            {},  # missing
            {"aapEndpoint": ""},  # empty
            {"aapEndpoint": None},  # wrong type
            {"aapEndpoint": "http://aap.example.com"},  # not https
            {"aapEndpoint": "https://user:pass@aap.example.com"},  # embedded credentials
            {"aapEndpoint": "https://aap.example.com/prefix"},  # path
            {"aapEndpoint": "https://aap.example.com/?q=1"},  # query
            {"aapEndpoint": "https://aap.example.com/#f"},  # fragment
            {"aapEndpoint": "not a url"},  # malformed
        ],
    )
    def test_invalid_endpoint_is_policy_denied(self, runtime_context: dict) -> None:
        with pytest.raises(ContainerInvocationError) as exc_info:
            validate_aap_endpoint(runtime_context)
        assert exc_info.value.code == FailureCode.POLICY_DENIED
        assert exc_info.value.retryable is False
        assert exc_info.value.safe_message  # user-safe message present


class TestResolveAapAuth:
    def test_token_wins_over_basic(self) -> None:
        headers, basic = resolve_aap_auth({"token": "tok", "username": "u", "password": "p"})
        assert headers == {"Authorization": "Bearer tok"}
        assert basic is None

    def test_basic_auth(self) -> None:
        headers, basic = resolve_aap_auth({"username": "u", "password": "p"})
        assert headers == {}
        assert basic == ("u", "p")

    def test_basic_auth_without_password_is_rejected(self) -> None:
        with pytest.raises(ContainerInvocationError) as exc_info:
            resolve_aap_auth({"username": "u"})
        assert exc_info.value.code == FailureCode.POLICY_DENIED

    def test_empty_credential_is_rejected(self) -> None:
        with pytest.raises(ContainerInvocationError) as exc_info:
            resolve_aap_auth({})
        assert exc_info.value.code == FailureCode.POLICY_DENIED


class TestResolveAapConnection:
    def test_full_connection_token(self) -> None:
        connection = resolve_aap_connection(
            validate_aap_integration(_VALID_RUNTIME_CONTEXT), {"token": "tok"}
        )
        assert connection.base_url == "https://aap.example.com"
        assert connection.auth_headers == {"Authorization": "Bearer tok"}
        assert connection.basic_auth is None
        assert connection.verify_ssl is True
        assert connection.ca_certificate is None

    def test_ca_certificate_carried_from_integration(self) -> None:
        ca = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
        connection = resolve_aap_connection(
            validate_aap_integration({**_VALID_RUNTIME_CONTEXT, "aapCaCertificate": ca}),
            {"token": "tok"},
        )
        assert connection.ca_certificate == ca
        assert connection.verify_ssl is True

    def test_ca_certificate_requires_verification(self) -> None:
        ca = "-----BEGIN CERTIFICATE-----\nabc\n-----END CERTIFICATE-----\n"
        with pytest.raises(ContainerInvocationError) as exc_info:
            resolve_aap_connection(
                validate_aap_integration(
                    {**_VALID_RUNTIME_CONTEXT, "aapVerifySsl": False, "aapCaCertificate": ca}
                ),
                {"token": "tok"},
            )
        assert exc_info.value.code == FailureCode.POLICY_DENIED

    def test_missing_credential_is_policy_denied(self) -> None:
        with pytest.raises(ContainerInvocationError) as exc_info:
            resolve_aap_connection(validate_aap_integration(_VALID_RUNTIME_CONTEXT), None)
        assert exc_info.value.code == FailureCode.POLICY_DENIED

    def test_non_boolean_verify_flag_is_rejected(self) -> None:
        with pytest.raises(ContainerInvocationError):
            resolve_aap_connection(
                validate_aap_integration({**_VALID_RUNTIME_CONTEXT, "aapVerifySsl": "yes"}),
                {"token": "tok"},
            )


class TestAapWorkflowStepRun:
    @respx.mock
    def test_run_executes_the_workflow_end_to_end(self) -> None:
        step = AapWorkflowStep()
        context = _credential_context({"token": "secret-token"})
        respx.post(
            "https://aap.example.com/api/controller/v2/workflow_job_templates/42/launch/"
        ).mock(return_value=httpx.Response(201, json={"id": 7}))
        respx.get("https://aap.example.com/api/controller/v2/workflow_jobs/7/").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 7,
                    "status": "successful",
                    "artifacts": {},
                    "created": "2026-01-01T00:00:00Z",
                    "started": "2026-01-01T00:00:01Z",
                    "finished": "2026-01-01T00:00:02Z",
                },
            )
        )
        output = step.run(AapWorkflowInput(workflow_job_template_id=42), context)
        assert output.status == "completed"
        assert output.workflow_job_id == 7

    def test_missing_endpoint_fails_before_credential_access(self) -> None:
        step = AapWorkflowStep()
        context = ExecutionContext(invocation_id="no-endpoint")._with_credentials(
            (AAP_CREDENTIAL_NAME,)
        )
        with pytest.raises(StepFailure) as exc_info:
            step.run(AapWorkflowInput(workflow_job_template_id=42), context)
        assert exc_info.value.code == FailureCode.POLICY_DENIED.value
        assert "endpoint" in exc_info.value.status_message.lower()

    @respx.mock
    def test_logs_do_not_contain_credential_values(self, caplog: pytest.LogCaptureFixture) -> None:
        step = AapWorkflowStep()
        token = "super-secret-token-value"
        context = _credential_context({"token": token})
        respx.post(
            "https://aap.example.com/api/controller/v2/workflow_job_templates/42/launch/"
        ).mock(return_value=httpx.Response(401, json={}))
        with caplog.at_level(logging.DEBUG, logger="syntara_node_aap_workflow"):
            with pytest.raises(StepFailure):
                step.run(AapWorkflowInput(workflow_job_template_id=42), context)
        for record in caplog.records:
            assert token not in record.getMessage()
