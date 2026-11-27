"""AAP integration endpoint and credential resolution for the Workflow step.

Mirrors ``resolve_aap_auth`` in the in-process adapter
(``aap_common.py``): the integration owns the URL and TLS material, the
credential owns authentication. Neither is author input — AO resolves the
integration and credential and supplies them through the SDK runtime:

- ``runtime_context["aapEndpoint"]`` — HTTPS AAP gateway base URL
- ``runtime_context["aapVerifySsl"]`` — TLS verification flag (default true)
- ``runtime_context["aapCaCertificate"]`` — optional PEM-encoded CA certificate
- the declared ``syntara.aap.credential`` credential document (token or basic auth)

Logging policy (plan Step 2, input to the AAP-98909 Product Security review):
never log tokens, ``extra_vars``, launch bodies, or AAP response bodies — only
status codes, safe body keys, and the endpoint host.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from syntara_plugin.runtime import ContainerInvocationError, FailureCode

logger = logging.getLogger(__name__)

# Logical credential name: the integration's credentialTypes entry (recipe
# spec.id), which is what the SDK runtime restricts credential requests to.
AAP_CREDENTIAL_NAME = "syntara.aap.credential"

# Runtime-context keys supplied by the AO OpenShift integration (AAP-98873/98908).
AAP_ENDPOINT_CONTEXT_KEY = "aapEndpoint"
AAP_VERIFY_SSL_CONTEXT_KEY = "aapVerifySsl"
AAP_CA_CERTIFICATE_CONTEXT_KEY = "aapCaCertificate"


@dataclass(frozen=True)
class AapIntegration:
    """Validated AAP integration settings (endpoint and TLS material)."""

    base_url: str
    verify_ssl: bool
    ca_certificate: str | None


@dataclass(frozen=True)
class AapResolvedAuth:
    """Resolved AAP connection and authentication, mirroring the adapter."""

    base_url: str
    auth_headers: dict[str, str]
    basic_auth: tuple[str, str] | None
    verify_ssl: bool
    ca_certificate: str | None


def _endpoint_error(message: str) -> ContainerInvocationError:
    return ContainerInvocationError(
        FailureCode.POLICY_DENIED,
        message,
        retryable=False,
        outcome="not_started",
    )


def validate_aap_endpoint(runtime_context: Mapping[str, Any]) -> str:
    """Require a configured HTTPS AAP gateway base URL.

    All AAP requests target this endpoint; the step never accepts a URL from
    step input, so the endpoint-credential destination guard is inherent: a
    credentialed call cannot be redirected to an arbitrary origin.
    """
    endpoint = runtime_context.get(AAP_ENDPOINT_CONTEXT_KEY)
    if not isinstance(endpoint, str) or not endpoint:
        raise _endpoint_error("AAP integration is not configured: no AAP endpoint was supplied.")
    try:
        parsed = urlsplit(endpoint)
    except ValueError as exc:
        raise _endpoint_error("The configured AAP endpoint is not a valid URL.") from exc

    if parsed.scheme.lower() != "https":
        raise _endpoint_error("The configured AAP endpoint must use https://.")
    if not parsed.hostname:
        raise _endpoint_error("The configured AAP endpoint must specify a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise _endpoint_error("The configured AAP endpoint must not embed credentials.")
    if parsed.query or parsed.fragment:
        raise _endpoint_error("The configured AAP endpoint must not include a query or fragment.")
    if parsed.path not in ("", "/"):
        raise _endpoint_error("The configured AAP endpoint must be a bare origin.")
    # Log the host only — never credentials, paths, or the full URL.
    logger.info("AAP endpoint configured (host=%s)", parsed.hostname)
    return endpoint


def _require_tls_settings(runtime_context: Mapping[str, Any]) -> tuple[bool, str | None]:
    verify_ssl = runtime_context.get(AAP_VERIFY_SSL_CONTEXT_KEY, True)
    if not isinstance(verify_ssl, bool):
        raise _endpoint_error("The configured AAP TLS verification flag must be a boolean.")
    ca_certificate = runtime_context.get(AAP_CA_CERTIFICATE_CONTEXT_KEY)
    if ca_certificate is not None and not isinstance(ca_certificate, str):
        raise _endpoint_error("The configured AAP CA certificate must be a string.")
    if ca_certificate is not None and "BEGIN CERTIFICATE" not in ca_certificate:
        raise _endpoint_error("The configured AAP CA certificate is not PEM-encoded.")
    if not verify_ssl and ca_certificate is not None:
        raise _endpoint_error(
            "The configured AAP CA certificate requires TLS verification to be enabled."
        )
    return verify_ssl, ca_certificate


def resolve_aap_auth(
    credential_doc: Mapping[str, Any],
) -> tuple[dict[str, str], tuple[str, str] | None]:
    """Extract auth from the credential document, matching the adapter's shapes.

    Token (Bearer/OAuth) wins over basic auth, as in ``get_aap_auth_from_credentials``.
    """
    token = credential_doc.get("token")
    if isinstance(token, str) and token:
        return {"Authorization": f"Bearer {token}"}, None

    username = credential_doc.get("username")
    password = credential_doc.get("password")
    if isinstance(username, str) and username:
        if not isinstance(password, str) or not password:
            raise ContainerInvocationError(
                FailureCode.POLICY_DENIED,
                "The configured AAP basic-auth credential is missing its password.",
                retryable=False,
                outcome="not_started",
            )
        return {}, (username, password)

    raise ContainerInvocationError(
        FailureCode.POLICY_DENIED,
        "The configured AAP credential contains no usable authentication fields.",
        retryable=False,
        outcome="not_started",
    )


def validate_aap_integration(runtime_context: Mapping[str, Any]) -> AapIntegration:
    """Validate the integration endpoint and TLS material without touching credentials."""
    base_url = validate_aap_endpoint(runtime_context)
    verify_ssl, ca_certificate = _require_tls_settings(runtime_context)
    return AapIntegration(base_url=base_url, verify_ssl=verify_ssl, ca_certificate=ca_certificate)


def resolve_aap_connection(
    integration: AapIntegration, credential_doc: Mapping[str, Any] | None
) -> AapResolvedAuth:
    """Resolve the full AAP connection: endpoint, TLS material, and auth."""
    if credential_doc is None:
        raise ContainerInvocationError(
            FailureCode.POLICY_DENIED,
            "AAP integration is not configured: no AAP credential was supplied.",
            retryable=False,
            outcome="not_started",
        )
    auth_headers, basic_auth = resolve_aap_auth(credential_doc)
    # Auth shape only — never the values.
    logger.info("AAP auth resolved (mode=%s)", "token" if auth_headers else "basic")
    return AapResolvedAuth(
        base_url=integration.base_url,
        auth_headers=auth_headers,
        basic_auth=basic_auth,
        verify_ssl=integration.verify_ssl,
        ca_certificate=integration.ca_certificate,
    )
