"""AAP Workflow execution logic: resolve, launch, and observe the parent job.

Mirrors the in-process adapter (``aap_workflow_job_template_activity.py`` and
``aap_common.py``) with the failure semantics recorded in the plan:

- Template/inventory/label resolution happens before launch; connection loss
  or a retryable HTTP status **before** the launch POST is sent maps to
  ``UPSTREAM_TRANSIENT`` (retryable, bounded internal retries).
- Anything after the launch POST is sent is non-retryable: a lost launch
  response is reconciled through a per-attempt correlation token carried in a
  reserved ``extra_vars`` key — workflow jobs for the template carrying the
  token in a pre-terminal state are adopted, never re-launched. At most one
  further launch follows a lookup that finds nothing.
- Only the parent workflow job is polled; the parent's terminal status is the
  step's terminal status. Poll-time 429/500/502/503/504 and connection errors
  retry inside the loop with a bounded consecutive-error cap.
- A parent that reaches a failure status is a completed observation, returned
  as the failed branch of the result union (recorded decision for AAP-98908:
  the SDK ABI cannot attach the preserved output fields to a ``StepFailure``,
  so AO maps ``status == "failed"`` to node failure).
- Cancellation requests a best-effort AAP cancel and reports ``CANCELLED``;
  it never claims the workflow or its children have stopped.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import uuid
from collections.abc import Awaitable, Callable
from threading import Event
from time import monotonic
from typing import Any

import httpx
from syntara_plugin.runtime import ContainerInvocationError, FailureCode

from ._auth import AapResolvedAuth
from ._models import AapWorkflowInput, AapWorkflowOutput
from ._result import bound_result_size, build_workflow_job_url

logger = logging.getLogger(__name__)

AAP_API_PREFIX = "/api/controller/v2"

# Implementation settings (not author inputs — plan Step 3).
POLL_INTERVAL_SECONDS = 5.0  # AAP recommendation, matches aap_poll_interval_seconds
DEFAULT_TIMEOUT_SECONDS = 3600  # matches the adapter's engine-timeout default
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)  # matches the adapter's client

# AAP terminal statuses (AAPJobTerminalStatus in aap_common.py).
TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})
FAILURE_STATUSES = frozenset({"failed", "error", "canceled"})

# Launch-phase retryable statuses (DEFAULT_RETRYABLE_ERROR_CODES — excludes 500:
# a launch 500 may be a real rejection). Poll-phase adds 500 (idempotent GET).
LAUNCH_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
TRANSIENT_POLL_STATUSES = LAUNCH_RETRYABLE_STATUSES | {500}
MAX_CONSECUTIVE_POLL_ERRORS = 5  # matches aap_common.py

# Pre-launch resolution retries for connection loss / retryable statuses.
MAX_RESOLUTION_ATTEMPTS = 3
RESOLUTION_BACKOFF_SECONDS = 1.0

# Uncertain-launch reconciliation (plan Step 5): each attempt carries a unique
# correlation token in a reserved extra_vars key. A lost launch response is
# reconciled by looking up workflow jobs for the template that carry the token
# in a pre-terminal state. Adopting "any running job for this template" is
# forbidden — it can bind another author's run.
LAUNCH_TOKEN_EXTRA_VAR = "_syntara_launch_token"
PRE_TERMINAL_STATUSES = frozenset({"new", "pending", "waiting", "running"})
MAX_LAUNCH_ATTEMPTS = 2  # first POST plus at most one further launch

# Label guards (aap_common.py security limits).
MAX_LABELS_PER_WORKFLOW = 100
MAX_LABEL_NAME_LENGTH = 512
LABEL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9 ._-]+$")


def _failure(
    code: FailureCode,
    message: str,
    *,
    retryable: bool = False,
    outcome: str = "not_completed",
) -> ContainerInvocationError:
    return ContainerInvocationError(code, message, retryable=retryable, outcome=outcome)


def _map_status_error(status_code: int, what: str) -> ContainerInvocationError:
    """Map an HTTP status from an authenticated AAP call that produced no job id."""
    if status_code == 401:
        return _failure(FailureCode.AUTHENTICATION_FAILED, f"{what}: HTTP 401.")
    if status_code == 403:
        return _failure(FailureCode.AUTHORIZATION_FAILED, f"{what}: HTTP 403.")
    if status_code == 404:
        return _failure(FailureCode.NOT_FOUND, f"{what}: HTTP 404.")
    return _failure(FailureCode.REMOTE_REJECTED, f"{what}: HTTP {status_code}.")


def _http_verify(connection: AapResolvedAuth) -> bool | ssl.SSLContext:
    """Build httpx's verify parameter, matching the adapter's tls_utils.

    httpx accepts a path (not PEM content) for custom CAs, so a custom CA is
    loaded into an ssl.SSLContext via cadata, as ``build_integration_httpx_verify``
    does in the in-process adapter.
    """
    if not connection.verify_ssl:
        return False
    if connection.ca_certificate and connection.ca_certificate.strip():
        context = ssl.create_default_context()
        context.load_verify_locations(cadata=connection.ca_certificate.strip())
        return context
    return True


class _AapClient:
    """Thin httpx wrapper carrying auth, TLS, and the endpoint."""

    def __init__(self, client: httpx.AsyncClient, connection: AapResolvedAuth) -> None:
        self._client = client
        self._connection = connection
        self._basic_auth = (
            httpx.BasicAuth(*connection.basic_auth) if connection.basic_auth else None
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        return await self._client.get(
            f"{self._connection.base_url}{path}",
            params=params,
            headers=self._connection.auth_headers,
            auth=self._basic_auth or httpx.USE_CLIENT_DEFAULT,
        )

    async def post(self, path: str, json_body: dict[str, Any] | None = None) -> httpx.Response:
        return await self._client.post(
            f"{self._connection.base_url}{path}",
            json=json_body,
            headers=self._connection.auth_headers,
            auth=self._basic_auth or httpx.USE_CLIENT_DEFAULT,
        )


def _cancelled(cancellation: Event | None) -> bool:
    return cancellation is not None and cancellation.is_set()


async def _lookup_single(
    aap: _AapClient, resource_path: str, name: str, organization: str, display: str
) -> int:
    """Resolve a resource by name + organization, requiring exactly one match."""
    response = await aap.get(
        f"{AAP_API_PREFIX}/{resource_path}/",
        {"name": name, "organization__name": organization},
    )
    if response.status_code in (401, 403, 404):
        raise _map_status_error(response.status_code, f"{display} lookup")
    if response.status_code in LAUNCH_RETRYABLE_STATUSES or response.status_code >= 500:
        # Pre-launch read; safe to surface as transient for the retry wrapper.
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT,
            f"{display} lookup failed: HTTP {response.status_code}.",
            retryable=True,
        )
    try:
        results = response.json().get("results", [])
    except ValueError:
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT, f"{display} lookup returned invalid data."
        ) from None
    if not results:
        raise _failure(
            FailureCode.NOT_FOUND,
            f"{display} '{name}' not found in organization '{organization}'.",
            outcome="not_started",
        )
    if len(results) > 1:
        raise _failure(
            FailureCode.REMOTE_REJECTED,
            f"Multiple {display}s named '{name}' found in organization '{organization}'.",
            outcome="not_started",
        )
    return int(results[0]["id"])


async def _with_resolution_retries[T](operation: Callable[[], Awaitable[T]], what: str) -> T:
    """Retry pre-launch reads on transient failures (connection loss included)."""
    last_error: ContainerInvocationError | None = None
    for attempt in range(MAX_RESOLUTION_ATTEMPTS):
        try:
            return await operation()
        except httpx.HTTPError as exc:
            last_error = _failure(
                FailureCode.UPSTREAM_TRANSIENT,
                f"{what} failed to connect: {type(exc).__name__}.",
                retryable=True,
            )
        except ContainerInvocationError as exc:
            if not exc.retryable:
                raise
            last_error = exc
        logger.info(
            "%s failed transiently, retrying (attempt=%s): %s: %s",
            what,
            attempt + 1,
            type(last_error).__name__ if last_error else "unknown",
            str(last_error)[:200] if last_error else "",
        )
        await asyncio.sleep(RESOLUTION_BACKOFF_SECONDS)
    raise _failure(
        FailureCode.UPSTREAM_TRANSIENT,
        f"{what} failed after {MAX_RESOLUTION_ATTEMPTS} attempts.",
        retryable=True,
    ) from last_error


async def _resolve_template_id(aap: _AapClient, inputs: AapWorkflowInput) -> int:
    if inputs.workflow_job_template_id is not None:
        return inputs.workflow_job_template_id
    return await _with_resolution_retries(
        lambda: _lookup_single(
            aap,
            "workflow_job_templates",
            inputs.workflow_job_template_name or "",
            inputs.organization_name or "",
            "workflow job template",
        ),
        "workflow job template lookup",
    )


async def _resolve_inventory_id(aap: _AapClient, inputs: AapWorkflowInput) -> int | None:
    if inputs.inventory_id is not None:
        return inputs.inventory_id
    if inputs.inventory_name is not None:
        return await _with_resolution_retries(
            lambda: _lookup_single(
                aap,
                "inventories",
                inputs.inventory_name or "",
                inputs.organization_name or "",
                "inventory",
            ),
            "inventory lookup",
        )
    return None


def _validate_labels(labels: list[str]) -> None:
    if len(labels) > MAX_LABELS_PER_WORKFLOW:
        raise _failure(
            FailureCode.INVALID_INPUT,
            f"Cannot process more than {MAX_LABELS_PER_WORKFLOW} labels per workflow.",
            outcome="not_started",
        )
    for name in labels:
        if "\x00" in name:
            raise _failure(
                FailureCode.INVALID_INPUT,
                "Label names must not contain null bytes.",
                outcome="not_started",
            )
        if len(name) > MAX_LABEL_NAME_LENGTH:
            raise _failure(
                FailureCode.INVALID_INPUT,
                f"Label name exceeds {MAX_LABEL_NAME_LENGTH} characters.",
                outcome="not_started",
            )
        if not LABEL_NAME_PATTERN.fullmatch(name):
            raise _failure(
                FailureCode.INVALID_INPUT,
                "Label names may only contain letters, digits, spaces, ._-",
                outcome="not_started",
            )


async def _resolve_organization_id(aap: _AapClient, inputs: AapWorkflowInput) -> int:
    if inputs.organization_id is not None:
        return inputs.organization_id
    response = await aap.get(f"{AAP_API_PREFIX}/organizations/", {"name": inputs.organization_name})
    if response.status_code in (401, 403, 404):
        raise _map_status_error(response.status_code, "organization lookup")
    try:
        results = response.json().get("results", [])
    except ValueError:
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT, "Organization lookup returned invalid data."
        ) from None
    if len(results) != 1:
        raise _failure(
            FailureCode.NOT_FOUND,
            f"Organization '{inputs.organization_name}' not found.",
            outcome="not_started",
        )
    return int(results[0]["id"])


async def _resolve_label_id(aap: _AapClient, name: str, org_id: int) -> int:
    """Return one label id, creating the label when AAP does not have it yet."""
    response = await aap.get(f"{AAP_API_PREFIX}/labels/", {"name": name, "organization": org_id})
    if response.status_code in (401, 403, 404):
        raise _map_status_error(response.status_code, f"label '{name}' lookup")
    if response.status_code >= 400:
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT,
            f"Label '{name}' lookup failed: HTTP {response.status_code}.",
            retryable=True,
        )
    try:
        results = response.json().get("results", [])
    except ValueError:
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT, "Label lookup returned invalid data."
        ) from None
    if results:
        return int(results[0]["id"])
    # Label does not exist — create it (AAP's on-launch behavior).
    create = await aap.post(f"{AAP_API_PREFIX}/labels/", {"name": name, "organization": org_id})
    if create.status_code == 409:
        retry = await aap.get(f"{AAP_API_PREFIX}/labels/", {"name": name, "organization": org_id})
        results = retry.json().get("results", []) if retry.status_code == 200 else []
        if not results:
            raise _failure(FailureCode.REMOTE_REJECTED, f"Label '{name}' creation conflicted.")
        return int(results[0]["id"])
    if create.status_code not in (200, 201):
        raise _map_status_error(create.status_code, f"label '{name}' creation")
    return int(create.json()["id"])


async def _resolve_label_ids(aap: _AapClient, inputs: AapWorkflowInput) -> list[int]:
    """Resolve label names to IDs, creating missing labels (adapter behavior)."""
    _validate_labels(inputs.labels)
    if not inputs.labels:
        return []
    org_id = await _with_resolution_retries(
        lambda: _resolve_organization_id(aap, inputs), "organization lookup"
    )
    label_ids: list[int] = []
    for name in inputs.labels:
        label_ids.append(await _resolve_label_id(aap, name, org_id))
    return label_ids


def _build_launch_body(inputs: AapWorkflowInput, inventory_id: int | None) -> dict[str, Any]:
    """Build the launch body, matching the adapter's _LAUNCH_BODY_FIELDS."""
    body: dict[str, Any] = {}
    if inventory_id is not None:
        body["inventory"] = inventory_id
    # Truthiness skips None/[]/{}/"" — same as the adapter.
    if inputs.extra_vars:
        body["extra_vars"] = inputs.extra_vars
    if inputs.limit:
        body["limit"] = inputs.limit
    if inputs.scm_branch:
        body["scm_branch"] = inputs.scm_branch
    if inputs.tags:
        body["job_tags"] = inputs.tags  # AAP expects job_tags, not tags
    if inputs.skip_tags:
        body["skip_tags"] = inputs.skip_tags
    return body


async def _find_token_job(aap: _AapClient, template_id: int, token: str) -> int | None:
    """Look up workflow jobs for the template carrying this attempt's token.

    Returns the job id when exactly one pre-terminal match exists. Never
    matches on status alone — adopting "any running job for this template"
    could bind another author's run. Returns ``None`` when no match exists.
    Raises ``OUTCOME_UNKNOWN`` when the lookup itself fails or is ambiguous.
    """
    lookup_url = f"{AAP_API_PREFIX}/workflow_job_templates/{template_id}/jobs/"
    try:
        response = await aap.get(lookup_url, {"page_size": 100})
    except httpx.HTTPError as exc:
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            "The AAP workflow launch response was lost and the reconciliation lookup failed.",
        ) from exc
    if response.status_code != 200:
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            "The AAP workflow launch response was lost and the reconciliation "
            f"lookup failed: HTTP {response.status_code}.",
        )
    try:
        results = response.json().get("results", [])
    except ValueError:
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            "The AAP workflow launch response was lost and the reconciliation "
            "lookup returned invalid data.",
        ) from None

    matches = [
        int(job["id"])
        for job in results
        if isinstance(job, dict)
        and isinstance(job.get("extra_vars"), dict)
        and job["extra_vars"].get(LAUNCH_TOKEN_EXTRA_VAR) == token
        and str(job.get("status", "")).lower() in PRE_TERMINAL_STATUSES
    ]
    if not matches:
        return None
    if len(matches) > 1:
        # A unique token cannot legitimately match twice — ambiguous state.
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            "The AAP workflow launch reconciliation found multiple matching jobs.",
        )
    return matches[0]


async def _launch_attempt(
    aap: _AapClient,
    launch_url: str,
    body: dict[str, Any],
    template_id: int,
    token: str,
    attempt: int,
) -> int | None:
    """POST one launch attempt.

    Returns the job id when the launch or a reconciliation succeeds. Returns
    ``None`` when the response was lost, no correlated job exists, and one
    further POST is still allowed.
    """
    try:
        response = await aap.post(launch_url, body)
    except httpx.ConnectError as exc:
        # The connection was never established — the POST was not delivered.
        raise _failure(
            FailureCode.UPSTREAM_TRANSIENT,
            "Failed to connect to AAP for the workflow launch.",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        # The POST may have been sent — reconcile via the token before any
        # further POST. Adopt a matching job; never adopt on status alone.
        adopted = await _find_token_job(aap, template_id, token)
        if adopted is not None:
            logger.info(
                "Adopted correlated AAP workflow job after lost launch response "
                "(template_id=%s, job_id=%s)",
                template_id,
                adopted,
            )
            return adopted
        if attempt >= MAX_LAUNCH_ATTEMPTS:
            raise _failure(
                FailureCode.OUTCOME_UNKNOWN,
                "The AAP workflow launch request was sent but its response was "
                "lost, and no correlated workflow job was found.",
            ) from exc
        logger.info(
            "Launch response lost with no correlated job; one further launch "
            "attempt allowed (attempt=%s)",
            attempt,
        )
        return None

    if response.status_code in LAUNCH_RETRYABLE_STATUSES:
        # A received retryable status proves the launch did not happen; still
        # non-retryable once a launch request was sent (plan Step 5).
        raise _failure(
            FailureCode.REMOTE_REJECTED,
            f"Workflow job template launch rejected: HTTP {response.status_code}.",
        )
    if response.status_code >= 400:
        raise _map_status_error(response.status_code, "workflow job template launch")
    try:
        job_id = int(response.json()["id"])
    except (ValueError, KeyError) as exc:
        raise _failure(
            FailureCode.REMOTE_REJECTED,
            "Workflow job template launch returned no job id.",
        ) from exc
    logger.info(
        "Launched AAP workflow job template (template_id=%s, job_id=%s)", template_id, job_id
    )
    return job_id


async def _launch(
    aap: _AapClient, inputs: AapWorkflowInput, cancellation: Event | None
) -> tuple[int, int]:
    """Resolve overrides and launch. Returns (job_id, template_id)."""
    if _cancelled(cancellation):
        raise _failure(
            FailureCode.CANCELLED,
            "The AAP workflow step was cancelled before launching.",
            outcome="not_started",
        )
    if LAUNCH_TOKEN_EXTRA_VAR in inputs.extra_vars:
        raise _failure(
            FailureCode.INVALID_INPUT,
            f"extra_vars must not use the reserved key '{LAUNCH_TOKEN_EXTRA_VAR}'.",
            outcome="not_started",
        )
    template_id = await _resolve_template_id(aap, inputs)
    inventory_id = await _resolve_inventory_id(aap, inputs)
    label_ids = await _resolve_label_ids(aap, inputs)

    body = _build_launch_body(inputs, inventory_id)
    if label_ids:
        body["labels"] = label_ids
    merged_extra_vars = dict(body.get("extra_vars") or {})
    launch_url = f"{AAP_API_PREFIX}/workflow_job_templates/{template_id}/launch/"

    for attempt in range(1, MAX_LAUNCH_ATTEMPTS + 1):
        # Each attempt owns a unique correlation token carried in extra_vars.
        token = uuid.uuid4().hex
        body["extra_vars"] = {**merged_extra_vars, LAUNCH_TOKEN_EXTRA_VAR: token}
        job_id = await _launch_attempt(aap, launch_url, body, template_id, token, attempt)
        if job_id is not None:
            return job_id, template_id

    raise AssertionError("unreachable launch loop exit")


async def _cancel_workflow_job(aap: _AapClient, job_id: int) -> None:
    """Best-effort cancel; failures never change the CANCELLED outcome."""
    try:
        await aap.post(f"{AAP_API_PREFIX}/workflow_jobs/{job_id}/cancel/")
    except httpx.HTTPError:
        logger.warning("Best-effort AAP cancel request failed (job_id=%s)", job_id)


def _raise_if_poll_deadline(job_id: int, deadline: float, last_error: str | None) -> None:
    """A healthy poll that runs out of budget is a timeout; errors until then are unknown."""
    if monotonic() < deadline:
        return
    if last_error is not None:
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            f"AAP workflow job {job_id} launched successfully but its completion "
            f"status is unknown — polling failed until the deadline. "
            f"Last error: {last_error}",
        )
    raise _failure(
        FailureCode.TIMEOUT,
        f"AAP workflow job {job_id} did not reach a terminal status before the timeout.",
    )


async def _fetch_workflow_job(aap: _AapClient, status_url: str, job_id: int) -> dict[str, Any]:
    """GET the workflow job. Transient statuses are raised as HTTP errors for the poll loop."""
    response = await aap.get(status_url)
    if response.status_code in TRANSIENT_POLL_STATUSES:
        raise httpx.HTTPStatusError(
            "transient poll status", request=response.request, response=response
        )
    if response.status_code in (401, 403):
        raise _map_status_error(response.status_code, "workflow job status poll")
    if response.status_code == 404:
        raise _failure(FailureCode.NOT_FOUND, f"AAP workflow job {job_id} disappeared.")
    if response.status_code >= 400:
        raise _failure(
            FailureCode.REMOTE_REJECTED,
            f"Workflow job status poll failed: HTTP {response.status_code}.",
        )
    job_data: dict[str, Any] = response.json()
    return job_data


def _record_poll_error(
    job_id: int, consecutive_errors: int, exc: httpx.HTTPError
) -> tuple[int, str]:
    """Count one transient poll failure and stop once the consecutive-error cap is reached."""
    consecutive_errors += 1
    last_error = str(exc) or type(exc).__name__
    logger.warning(
        "Transient error polling workflow job status (job_id=%s, consecutive=%s)",
        job_id,
        consecutive_errors,
    )
    if consecutive_errors >= MAX_CONSECUTIVE_POLL_ERRORS:
        raise _failure(
            FailureCode.OUTCOME_UNKNOWN,
            f"AAP workflow job {job_id} launched successfully but its completion "
            f"status is unknown — polling failed {consecutive_errors} consecutive "
            f"times. Last error: {last_error}",
        ) from exc
    return consecutive_errors, last_error


async def _poll_parent(
    aap: _AapClient, job_id: int, deadline: float, cancellation: Event | None
) -> dict[str, Any]:
    """Poll the parent workflow job until a terminal status.

    Timeout and lost contact are distinguished: poll errors that persist to the
    deadline (or past the consecutive-error cap) are ``OUTCOME_UNKNOWN``; a
    healthy poll loop that runs out of budget is ``TIMEOUT``. Neither path
    relaunches.
    """
    status_url = f"{AAP_API_PREFIX}/workflow_jobs/{job_id}/"
    consecutive_errors = 0
    last_error: str | None = None

    while True:
        if _cancelled(cancellation):
            await _cancel_workflow_job(aap, job_id)
            raise _failure(
                FailureCode.CANCELLED,
                "The AAP workflow observation was cancelled; an AAP cancel was requested.",
            )
        _raise_if_poll_deadline(job_id, deadline, last_error)
        try:
            job_data = await _fetch_workflow_job(aap, status_url, job_id)
        except httpx.HTTPError as exc:
            consecutive_errors, last_error = _record_poll_error(job_id, consecutive_errors, exc)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        consecutive_errors = 0
        last_error = None
        status = job_data.get("status")
        if isinstance(status, str) and status.lower() in TERMINAL_STATUSES:
            logger.info("AAP workflow job reached terminal status (job_id=%s)", job_id)
            return job_data
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _result_from_job_data(
    job_data: dict[str, Any], job_id: int, base_url: str
) -> AapWorkflowOutput:
    status = str(job_data.get("status", "")).lower()
    job_url = build_workflow_job_url(base_url, job_id)
    if status in FAILURE_STATUSES:
        # Completed observation of a failed parent: the failed branch of the
        # result union (recorded decision for AAP-98908).
        return AapWorkflowOutput(
            status="failed",
            workflow_job_id=job_id,
            workflow_job_url=job_url,
            workflow_job_status=status,
            artifacts=job_data.get("artifacts") or {},
            created=job_data.get("created"),
            started=job_data.get("started"),
            finished=job_data.get("finished"),
            error=f"AAP workflow job {job_id} finished with status: {status}",
        )
    return AapWorkflowOutput(
        status="completed",
        workflow_job_id=job_id,
        workflow_job_url=job_url,
        workflow_job_status=status,
        artifacts=job_data.get("artifacts") or {},
        created=job_data.get("created") or "",
        started=job_data.get("started") or "",
        finished=job_data.get("finished") or "",
    )


async def _execute(
    inputs: AapWorkflowInput, connection: AapResolvedAuth, cancellation: Event | None
) -> AapWorkflowOutput:
    verify = _http_verify(connection)
    async with httpx.AsyncClient(verify=verify, timeout=HTTP_TIMEOUT, trust_env=False) as client:
        aap = _AapClient(client, connection)
        job_id, _template_id = await _launch(aap, inputs, cancellation)
        deadline = monotonic() + (
            inputs.timeout if inputs.timeout is not None else DEFAULT_TIMEOUT_SECONDS
        )
        job_data = await _poll_parent(aap, job_id, deadline, cancellation)
        return bound_result_size(_result_from_job_data(job_data, job_id, connection.base_url))


def execute_aap_workflow(
    inputs: AapWorkflowInput,
    connection: AapResolvedAuth,
    cancellation: Event | None = None,
) -> AapWorkflowOutput:
    """Run the async AAP workflow operation from the synchronous SDK hook."""
    if cancellation is not None and cancellation.is_set():
        raise _failure(
            FailureCode.CANCELLED,
            "The AAP workflow step was cancelled before starting.",
            outcome="not_started",
        )
    return asyncio.run(_execute(inputs, connection, cancellation))
