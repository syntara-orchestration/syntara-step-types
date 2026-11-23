# AAP Workflow container — compatibility record vs. the in-process step

Reference for [AAP-98908](https://redhat.atlassian.net/browse/AAP-98908) (AO
invocation and downstream projection) and
[AAP-95478](https://redhat.atlassian.net/browse/AAP-95478) (compatibility
rules). Every intentional difference between this container step and the
in-process adapter (`aap_workflow_job_template_activity.py` +
`aap_common.py` in syntara) is listed here. Items withheld under open
decisions are marked and must not be resolved implicitly.

## Preserved behavior

- **Inputs**: the full author-facing parameter surface — template by id or
  name+organization (id wins), `extra_vars`, `limit`, `scm_branch`, `tags`
  (sent to AAP as `job_tags`), `skip_tags`, `labels`, inventory override by
  id or name (name requires organization), `timeout` — with the adapter's
  conditional validation.
- **Outputs**: the adapter's discriminated result union on `status`
  (Completed/Failed) with the same fields — `workflow_job_id`,
  `workflow_job_url` (derived: `{base_url}/execution/jobs/workflow/{id}/output`),
  `workflow_job_status`, `artifacts`, `created`/`started`/`finished`, and
  `error` on the failed branch. Null fields are omitted from serialization,
  matching the adapter's serialized shape.
- **Gateway API**: `/api/controller/v2/` prefix on the integration base URL,
  same endpoints (template/inventory/organization/label lookups, launch,
  parent job polling, cancel). Legacy `/api/v2/` remains an open
  supported-version question (plan open decision 2).
- **Parent-only observation**: the parent workflow job's terminal status is
  the step's terminal status; child jobs are never polled directly.
- **Labels**: resolved to IDs, created when missing, with the adapter's
  security guards (max 100 per workflow, name pattern, 512-char limit,
  null-byte check), including 409-conflict re-lookup.
- **Polling**: 5-second interval (AAP recommendation), transient poll errors
  (429/500/502/503/504, connection loss) retried with a 5-consecutive-error
  cap; timeout vs. lost-contact distinguished in the failure message.
- **Cancellation**: best-effort `POST .../workflow_jobs/{id}/cancel/` then a
  cancelled outcome; a failed cancel request does not change the outcome; no
  claim is made that child jobs have stopped.
- **Timeout default**: 3600 s when the author does not set `timeout`.

## Intentional differences

1. **Execution boundary.** The in-process Temporal activity becomes a
   cold-start, single-invocation container running the SDK synchronous gRPC
   `ContainerService`. AO dispatches it through the shared OpenShift path
   (AAP-98873/AAP-98908); the step makes no dispatch assumptions.
2. **Integration and credential resolution.** `integration_id` and
   `credential_id` are AO-only; they stay on the AO side. The container
   receives the endpoint and TLS material through `runtime_context`
   (`aapEndpoint`, `aapVerifySsl`, `aapCaCertificate`) and the credential
   through the declared `syntara.aap.credential` binding (token, or
   username+password — token wins, matching `resolve_aap_auth`). The adapter
   read `aap_oauth_token`/`aap_username`/`aap_password` from resolved
   credential extra_vars; AO maps those fields onto the container recipe's
   `token`/`username`/`password`.
3. **Failed-parent delivery.** The adapter fails the Temporal node with an
   `ApplicationError` that carries the output document as data. The SDK ABI
   cannot attach the output fields to a failure, so the container returns the
   failed branch of the result union as a **successful SDK result**
   (`status == "failed"`, `error` set). **AAP-98908 must map
   `status == "failed"` to node failure** to preserve today's behavior.
4. **Correlation token.** Every launch carries a per-attempt `uuid4` token in
   the reserved `extra_vars` key `_syntara_launch_token` (inputs using that
   key are rejected). The token is visible in the launched workflow job's
   `extra_vars`; templates that enumerate extra_vars will see it. Needs an
   AAP-95478 record.
5. **Uncertain-launch handling.** The adapter treated a launch-phase
   connection error as a non-retryable failure (or retryable
   `ApplicationError` for some shapes). The container distinguishes: a
   connection that was never established is a retryable
   `UPSTREAM_TRANSIENT` (bounded internal retries); a lost response after
   the POST may have been sent is reconciled through the correlation token
   (adopt the matching pre-terminal job, never re-POST blindly, at most one
   further launch after an empty lookup). Ambiguous or failed lookups are
   `OUTCOME_UNKNOWN`.
6. **Launch-phase retryable statuses.** A *received* 429/502/503/504 on
   launch proves the launch did not happen; the adapter surfaced these as
   retryable. The container reports them as non-retryable `REMOTE_REJECTED`
   (once a launch request was sent, the step never re-sends it). A 500 on
   launch is non-retryable in both.
7. **Result size bound.** The SDK ABI permits 1 MiB for the serialized
   result. `artifacts` is truncated first — largest keys dropped, listed
   under `__syntara_truncated_artifacts__` — and identity fields are never
   dropped. The adapter had no equivalent bound inside the activity.
8. **Execution budget enforcement.** The SDK runtime enforces the invocation
   budget at the ABI level and can emit its own `TIMEOUT` failure; the
   executor returns its precise failure (with the workflow job id) before
   that budget expires.
9. **Audit events and heartbeats.** The adapter emitted audit events
   (`emit_launched`/`emit_failed`/`emit_completed`) and Temporal heartbeats
   with partial output. The container emits neither; AO owns audit and
   liveness (AAP-98908 scope).
10. **Poll interval configurability.** The adapter read
    `aap_poll_interval_seconds` from deployment settings; the container uses
    a fixed 5-second implementation constant. Promoting it to an author
    input would be a compatibility difference requiring an AAP-95478 record.
11. **Logging.** The adapter logged with structlog including safe body keys;
    the container logs endpoint host, auth mode, launch/adoption/poll
    milestones, and error class names only — never tokens, `extra_vars`,
    launch bodies, or AAP response bodies.

## Withheld under open decisions

- Child-result/artifact aggregation and nested/approval behavior (decision 1)
  — the parent is polled only; existing parent `artifacts` are projected.
- Parent/child cancellation semantics beyond the best-effort parent cancel
  (decision 2).
- Legacy `/api/v2/` support (decision 2).
- Approved OpenShift security context (decision 7) — the image runs as
  nonroot `65532` with a read-only-rootfs candidate; approval belongs to
  AAP-98909.

## Inputs for the AAP-98909 Product Security review

This story implements the credential, logging, and lifecycle behavior but does
not close the Product Security review. The review needs:

- **Runtime/transport model** — cold-start, single-invocation pods running the
  SDK synchronous gRPC `ContainerService`; gRPC over loopback port-forward;
  mutual TLS between launcher and workload through the SDK's TLS environment
  contract (production wiring tracked with AAP-98873/AAP-98908). Recorded in
  `docs/aap-workflow-sdk-verification.md` in this repository.
- **Security context** — candidate: nonroot user `65532:65532`, read-only
  root filesystem, all capabilities dropped, `allowPrivilegeEscalation:
  false`, no secrets as environment variables (credentials arrive through the
  SDK callback channel with leak scanning). **Not yet approved** — tracked as
  plan open decision 7.
- **Logging rules** — endpoint host, auth mode, launch/adoption/poll
  milestones, and error class names only; never tokens, `extra_vars`, launch
  bodies, or AAP response bodies. Enforced in `_auth.py`/`executor.py` and
  asserted by unit and integration tests (container-log leak checks).
- **Credential handling** — invocation-scoped, non-serializable bindings read
  via `context.credential()`; results, errors, and logs are scanned for
  credential values by the SDK runtime; the step never logs or returns them.
