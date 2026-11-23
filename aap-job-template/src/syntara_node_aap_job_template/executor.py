"""AAP Controller API operations for the job template step."""

from __future__ import annotations

import asyncio
import json
import re
import ssl
import time
from collections.abc import Mapping
from threading import Event
from typing import Any
from urllib.parse import urlsplit

import httpx

from ._models import AAPJobTemplateInput, AAPJobTemplateOutput

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_TERMINAL_STATUSES = frozenset({"successful", "failed", "error", "canceled"})
_MAX_LABELS = 100
_MAX_LABEL_LENGTH = 512
_LABEL_PATTERN = re.compile(r"^[a-zA-Z0-9 ._-]+$")
_MAX_OUTPUT_BYTES = 1_048_576


class AAPExecutionError(Exception):
    """Safe-to-report AAP error with retry classification."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class AAPCancelledError(AAPExecutionError):
    """Raised after a running Controller job is asked to cancel."""


class AAPTimeoutError(AAPExecutionError):
    """Raised when the configured job timeout expires."""


class AAPPolicyError(AAPExecutionError):
    """Raised when trusted connection or credential metadata is invalid."""


def _endpoint_and_tls(context_values: Mapping[str, Any]) -> tuple[str, bool | ssl.SSLContext]:
    endpoint = context_values.get("aapEndpoint")
    if not isinstance(endpoint, str):
        raise AAPPolicyError("The selected AAP integration endpoint is unavailable.")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise AAPPolicyError("The selected AAP integration has an invalid endpoint URL.") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise AAPPolicyError("The selected AAP integration must use a valid HTTPS base URL.")

    verify_ssl = context_values.get("aapVerifySSL", True)
    if not isinstance(verify_ssl, bool):
        raise AAPPolicyError("The selected AAP integration has an invalid TLS setting.")
    ca_certificate = context_values.get("aapCACertificate")
    if ca_certificate is not None:
        if not isinstance(ca_certificate, str) or not ca_certificate.strip() or not verify_ssl:
            raise AAPPolicyError(
                "The selected AAP integration has an invalid CA certificate setting."
            )
        try:
            verify: bool | ssl.SSLContext = ssl.create_default_context(cadata=ca_certificate)
        except (ssl.SSLError, ValueError) as exc:
            raise AAPPolicyError("The selected AAP integration CA certificate is invalid.") from exc
    else:
        verify = verify_ssl
    return endpoint.rstrip("/"), verify


def _auth(credential: dict[str, Any]) -> tuple[dict[str, str], httpx.BasicAuth | None]:
    fields = credential.get("extra_vars", credential)
    if not isinstance(fields, dict):
        raise AAPPolicyError("The configured AAP credential is invalid.")
    # Accept both the backend credential fields and their legacy injector names.
    token = fields.get("oauth_token", fields.get("aap_oauth_token"))
    username = fields.get("username", fields.get("aap_username"))
    password = fields.get("password", fields.get("aap_password", ""))
    if isinstance(token, str) and token:
        return {"Authorization": f"Bearer {token}"}, None
    if isinstance(username, str) and username and isinstance(password, str) and password:
        return {}, httpx.BasicAuth(username, password)
    raise AAPPolicyError("The configured AAP credential has no usable authentication fields.")


def _status_error(response: httpx.Response, operation: str) -> AAPExecutionError:
    code = response.status_code
    return AAPExecutionError(
        f"AAP {operation} failed with HTTP {code}.",
        retryable=code in _RETRYABLE_STATUS,
    )


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
    operation: str,
    retryable_network_error: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    try:
        response = await client.request(method, url, headers=headers, auth=basic_auth, **kwargs)
    except httpx.TimeoutException as exc:
        raise AAPExecutionError(
            f"AAP {operation} timed out.",
            retryable=retryable_network_error,
        ) from exc
    except httpx.HTTPError as exc:
        raise AAPExecutionError(
            f"AAP {operation} could not connect ({type(exc).__name__}).",
            retryable=retryable_network_error,
        ) from exc
    if response.is_error:
        raise _status_error(response, operation)
    return response


async def _lookup_by_name(
    client: httpx.AsyncClient,
    name: str,
    organization_name: str,
    resource: str,
    base_url: str,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
) -> int:
    response = await _request(
        client,
        "GET",
        f"{base_url}/api/controller/v2/{resource}/",
        headers=headers,
        basic_auth=basic_auth,
        operation=f"{resource.replace('_', ' ')} lookup",
        params={"name": name, "organization__name": organization_name},
    )
    try:
        results = response.json().get("results", [])
        if not isinstance(results, list):
            raise TypeError
    except (ValueError, AttributeError, TypeError) as exc:
        raise AAPExecutionError(
            f"AAP returned an invalid {resource.replace('_', ' ')} lookup response."
        ) from exc
    if len(results) != 1:
        label = resource.replace("_", " ").removesuffix("s")
        if not results:
            raise AAPExecutionError(
                f"{label.capitalize()} '{name}' was not found in organization "
                f"'{organization_name}'."
            )
        raise AAPExecutionError(
            f"Multiple {resource.replace('_', ' ')} named '{name}' were found in that organization."
        )
    try:
        return int(results[0]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AAPExecutionError(
            f"AAP returned an invalid ID for {resource.replace('_', ' ')} '{name}'."
        ) from exc


async def _organization_id(
    client: httpx.AsyncClient,
    name: str,
    base_url: str,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
) -> int:
    response = await _request(
        client,
        "GET",
        f"{base_url}/api/controller/v2/organizations/",
        headers=headers,
        basic_auth=basic_auth,
        operation="organization lookup",
        params={"name": name},
    )
    results = response.json().get("results", [])
    if not results:
        raise AAPExecutionError(f"Organization '{name}' was not found.")
    try:
        return int(results[0]["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AAPExecutionError("AAP returned an invalid organization ID.") from exc


async def _resolve_labels(
    client: httpx.AsyncClient,
    labels: list[str],
    config: AAPJobTemplateInput,
    base_url: str,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
) -> list[int]:
    if len(labels) > _MAX_LABELS:
        raise AAPExecutionError(f"Cannot process more than {_MAX_LABELS} labels per job.")
    for label in labels:
        if (
            not label
            or len(label) > _MAX_LABEL_LENGTH
            or "\x00" in label
            or not _LABEL_PATTERN.fullmatch(label)
        ):
            raise AAPExecutionError(
                "A label name is invalid; use up to 512 letters, numbers, spaces, dots, "
                "underscores, or hyphens."
            )

    org_id = config.organization_id
    if org_id is None and config.organization_name:
        org_id = await _organization_id(
            client,
            config.organization_name,
            base_url,
            headers,
            basic_auth,
        )
    if org_id is None:
        raise AAPExecutionError(
            "organization_id or organization_name is required to resolve labels."
        )

    label_ids: list[int] = []
    url = f"{base_url}/api/controller/v2/labels/"
    for label in labels:
        response = await _request(
            client,
            "GET",
            url,
            headers=headers,
            basic_auth=basic_auth,
            operation="label lookup",
            params={"name": label, "page_size": "200"},
        )
        results = [
            item
            for item in response.json().get("results", [])
            if item.get("organization") == org_id
        ]
        if results:
            label_ids.append(int(results[0]["id"]))
            continue

        try:
            response = await client.post(
                url,
                json={"name": label, "organization": org_id},
                headers=headers,
                auth=basic_auth or httpx.USE_CLIENT_DEFAULT,
            )
            if response.status_code == 409:
                retry = await _request(
                    client,
                    "GET",
                    url,
                    headers=headers,
                    basic_auth=basic_auth,
                    operation="label lookup after conflict",
                    params={"name": label, "page_size": "200"},
                )
                results = [
                    item
                    for item in retry.json().get("results", [])
                    if item.get("organization") == org_id
                ]
                if results:
                    label_ids.append(int(results[0]["id"]))
                    continue
                raise AAPExecutionError(
                    f"AAP label '{label}' was not found after a concurrent creation."
                )
            if response.is_error:
                raise _status_error(response, "label creation")
            label_ids.append(int(response.json()["id"]))
        except httpx.TimeoutException as exc:
            raise AAPExecutionError("AAP label creation timed out.") from exc
        except httpx.HTTPError as exc:
            raise AAPExecutionError(
                f"AAP label creation could not connect ({type(exc).__name__})."
            ) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise AAPExecutionError("AAP returned an invalid label creation response.") from exc
    return label_ids


async def _launch_job(
    client: httpx.AsyncClient,
    config: AAPJobTemplateInput,
    base_url: str,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
) -> tuple[int, int]:
    if config.job_template_id is not None:
        template_id = config.job_template_id
    else:
        assert config.job_template_name and config.organization_name
        template_id = await _lookup_by_name(
            client,
            config.job_template_name,
            config.organization_name,
            "job_templates",
            base_url,
            headers,
            basic_auth,
        )

    inventory_id = config.inventory_id
    if inventory_id is None and config.inventory_name:
        assert config.organization_name
        inventory_id = await _lookup_by_name(
            client,
            config.inventory_name,
            config.organization_name,
            "inventories",
            base_url,
            headers,
            basic_auth,
        )

    instance_group_id = config.instance_group_id
    if instance_group_id is None and config.instance_group_name:
        assert config.organization_name
        instance_group_id = await _lookup_by_name(
            client,
            config.instance_group_name,
            config.organization_name,
            "instance_groups",
            base_url,
            headers,
            basic_auth,
        )

    body: dict[str, Any] = {}
    if inventory_id is not None:
        body["inventory"] = inventory_id
    if instance_group_id is not None:
        body["instance_groups"] = [instance_group_id]
    fields = (
        ("job_credentials", "credentials"),
        ("extra_vars", "extra_vars"),
        ("limit", "limit"),
        ("tags", "job_tags"),
        ("skip_tags", "skip_tags"),
        ("verbosity", "verbosity"),
        ("job_type", "job_type"),
        ("forks", "forks"),
        ("job_slicing", "job_slice_count"),
        ("diff_mode", "diff_mode"),
    )
    for input_name, api_name in fields:
        value = getattr(config, input_name)
        if input_name == "verbosity":
            body[api_name] = value
        elif value:
            body[api_name] = value
    if config.labels:
        body["labels"] = await _resolve_labels(
            client,
            config.labels,
            config,
            base_url,
            headers,
            basic_auth,
        )

    response = await _request(
        client,
        "POST",
        f"{base_url}/api/controller/v2/job_templates/{template_id}/launch/",
        headers=headers,
        basic_auth=basic_auth,
        operation="job template launch",
        json=body,
    )
    try:
        job_id = int(response.json()["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AAPExecutionError("AAP returned an invalid job launch response.") from exc
    return job_id, template_id


async def _poll_job(
    client: httpx.AsyncClient,
    job_id: int,
    config: AAPJobTemplateInput,
    base_url: str,
    headers: dict[str, str],
    basic_auth: httpx.BasicAuth | None,
    cancellation: Event | None,
    started: float,
) -> dict[str, Any]:
    status_url = f"{base_url}/api/controller/v2/jobs/{job_id}/"
    consecutive_errors = 0
    last_error: AAPExecutionError | None = None
    poll_interval_value = 5

    while True:
        if cancellation is not None and cancellation.is_set():
            try:
                await client.post(
                    f"{base_url}/api/controller/v2/jobs/{job_id}/cancel/",
                    headers=headers,
                    auth=basic_auth or httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError:
                pass
            raise AAPCancelledError("AAP job execution was cancelled.")
        elapsed = time.monotonic() - started
        if elapsed >= config.timeout_seconds:
            if last_error is not None:
                raise AAPTimeoutError(
                    f"AAP job {job_id} was launched but polling failed repeatedly until timeout "
                    f"({config.timeout_seconds}s)."
                ) from last_error
            raise AAPTimeoutError(
                f"AAP job {job_id} timed out after {config.timeout_seconds} seconds."
            )

        try:
            response = await _request(
                client,
                "GET",
                status_url,
                headers=headers,
                basic_auth=basic_auth,
                operation=f"job {job_id} status poll",
                retryable_network_error=True,
            )
            data = response.json()
            if not isinstance(data, dict):
                raise AAPExecutionError(
                    f"AAP returned an invalid status response for job {job_id}."
                )
            status = data["status"]
            if not isinstance(status, str):
                raise AAPExecutionError(f"AAP returned an invalid status for job {job_id}.")
            if status.lower() in _TERMINAL_STATUSES:
                return data
            consecutive_errors = 0
            last_error = None
        except AAPExecutionError as exc:
            if not exc.retryable:
                raise
            consecutive_errors += 1
            last_error = exc
            if consecutive_errors >= 5:
                raise AAPExecutionError(
                    f"AAP job {job_id} launched successfully but status polling failed "
                    f"{consecutive_errors} consecutive times."
                ) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise AAPExecutionError(
                f"AAP returned an invalid status response for job {job_id}."
            ) from exc

        # Poll in short intervals so cancellation is observed promptly.
        wait_until = min(poll_interval_value, config.timeout_seconds - (time.monotonic() - started))
        waited = 0.0
        while waited < wait_until:
            if cancellation is not None and cancellation.is_set():
                break
            interval = min(0.25, wait_until - waited)
            await asyncio.sleep(interval)
            waited += interval


async def _execute(
    config: AAPJobTemplateInput,
    context_values: Mapping[str, Any],
    credential: dict[str, Any],
    cancellation: Event | None,
) -> AAPJobTemplateOutput:
    base_url, verify = _endpoint_and_tls(context_values)
    headers, basic_auth = _auth(credential)
    timeout = httpx.Timeout(30.0, connect=10.0)
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(verify=verify, timeout=timeout) as client:
            job_id, _template_id = await _launch_job(client, config, base_url, headers, basic_auth)
            job_data = await _poll_job(
                client,
                job_id,
                config,
                base_url,
                headers,
                basic_auth,
                cancellation,
                started,
            )
    except AAPExecutionError:
        raise
    except httpx.HTTPError as exc:
        raise AAPExecutionError(f"AAP request failed ({type(exc).__name__}).") from exc

    final_status = str(job_data.get("status", "error"))
    if final_status.lower() != "successful":
        raise AAPExecutionError(f"AAP job {job_id} failed with status: {final_status}.")

    artifacts = job_data.get("artifacts")
    created = job_data.get("created")
    started_timestamp = job_data.get("started")
    finished = job_data.get("finished")
    output = AAPJobTemplateOutput(
        job_id=job_id,
        job_url=f"{base_url}/execution/jobs/playbook/{job_id}/output",
        job_status=final_status,
        artifacts=artifacts if isinstance(artifacts, dict) else {},
        created=created if isinstance(created, str) else "",
        started=started_timestamp if isinstance(started_timestamp, str) else "",
        finished=finished if isinstance(finished, str) else "",
    )
    if len(json.dumps(output.model_dump()).encode()) > _MAX_OUTPUT_BYTES:
        raise AAPExecutionError("AAP job output exceeded the 1 MiB step result limit.")
    return output


def execute_aap_job_template(
    config: AAPJobTemplateInput,
    context_values: Mapping[str, Any],
    credential: dict[str, Any],
    cancellation: Event | None = None,
) -> AAPJobTemplateOutput:
    """Run the async launch-and-poll sequence from the SDK's sync hook."""
    if cancellation is not None and cancellation.is_set():
        raise AAPExecutionError("AAP job execution was cancelled before starting.")
    return asyncio.run(_execute(config, context_values, credential, cancellation))
