# AAP-98907 — Implementation Plan: SDK-based AAP Workflow container

Epic: [AAP-98893](https://redhat.atlassian.net/browse/AAP-98893) — AAP Workflow step: container execution and Red Hat Catalog release
Story: [AAP-98907](https://redhat.atlassian.net/browse/AAP-98907) — Implement the SDK-based AAP Workflow container

## Purpose

Extract the supported AAP Workflow step behavior into a self-contained workload so AO
can invoke it outside the control-plane worker. The step launches and observes a
workflow job template on an existing AAP instance; AAP owns the underlying workflow
and its child jobs.

The behavior to preserve is the in-process adapter in syntara
(`aap_workflow_job_template_activity.py`, `aap_common.py`, and
`aap_workflow_job_template.schema.json`). A field or outcome that differs from that
adapter is a compatibility difference: record it and get it approved against
AAP-95478 before shipping it. Do not invent a thinner contract and call the rest
an open question.

Out of scope (other stories in the epic): AO dispatch and downstream projection
(AAP-98908), architecture write-up and Product Security disposition (AAP-98909),
CI image gates (AAP-98910), Red Hat Catalog release (AAP-98911), operator
install/upgrade (AAP-98912). This story still implements the credential, logging,
and lifecycle behavior those stories will review.

## Repo patterns to replicate

`http-request/` is the package layout to copy (remote service + credentials):

- `src/syntara_node_<name>/_models.py` — Pydantic input/output models
- `executor.py` — async execution wrapped in `asyncio.run()` for the sync SDK hook;
  cancellation via `context.cancellation`; failure paths through
  `ContainerInvocationError(FailureCode.*, retryable=, outcome=)`
- `__init__.py` — `ActionStep[TInput, TOutput]` subclass; `StepFailure` mapping of
  `FailureCode` → `ErrorCategory`
- `manifest.yaml` / `integration.yaml` / credential recipe — step schema,
  integration type, credential declaration
- `syntara-plugin.yaml` / `runtime-bindings.yaml` / `plugin.yaml` — SDK
  build/publish configuration
- Top-level wiring: `NODES` / `SDK_PLUGINS` / `PYTHON_PACKAGES` in the `Makefile`,
  `tests/integration/*` tests

Copy the process model only after Step 0 selects it. `http-request`'s long-running
gRPC `ContainerService` on `0.0.0.0:50051`, its concurrency semaphore, and a
`python:3.12-slim` image are one candidate. The story requires a recorded
reconciliation with the execution-plane cold-start Job/gRPC decision before any of
those are treated as the Workflow runtime. Base image and `.tekton` pipelines are
provisional scaffolding until AAP-98910 and AAP-98911 confirm the release route.
The epic forbids prescribing a registry, certification path, base image, or build
system before that confirmation.

The image installs a pinned SDK version that CI can resolve from PyPI or an
approved prefetch. A local wheel is a development convenience. `COPY` of an SDK
checkout or of `.sdk-dist/` is not the image dependency path.

The AAP Job adapter (AAP-98892) is not in this repo. Build shared client, auth,
and polling helpers here so they can be lifted later, or coordinate with that
epic. Job and Workflow stay separate images. If that epic already defines the
`syntara.aap` integration type, use it; do not fork a second AAP integration.

## Step 0 — SDK acquisition, runtime reconciliation, pinning

Because ANSTRAT-2422 (published SDK runtime/contracts) has unconfirmed PyPI
publication status, SDK acquisition is an explicit first step. Scaffold the
entrypoint only after the reconciliation note below exists.

- [x] Confirm publication status of `syntara-plugin-contracts` /
  `syntara-plugin-sdk` on PyPI, or obtain the latest local build from the SDK team /
  `syntara-plugin-sdk` checkout. The repo's `.sdk-dist/` currently holds `0.1.0a2`
  wheels (stale — `http-request` pins `CONTRACTS_VERSION=0.1.0a5`).
  **Done: both published; latest `0.1.0a6` (2026-11-17).**
- [x] Decide the development source of truth: if unpublished, build the SDK from a
  pinned commit into `wheels/` or `.sdk-dist/` and record the exact version/commit.
  Development may proceed against a local checkout. The image and the release
  (AAP-98911) install that same version pin from PyPI or an approved prefetch.
  **Done: `0.1.0a6` from PyPI is the source of truth for dev, image, and release.**
- [x] Update `uv.lock` / dev dependency group (`syntara-plugin-sdk`) and the new
  step's `CONTRACTS_VERSION` pin to the agreed version.
  **Done: lock + venv bumped to `0.1.0a6`; `make check` passes (71 unit tests).
  See [aap-workflow-sdk-verification.md](aap-workflow-sdk-verification.md).**
- [x] Verify the contracts expose what the Workflow step needs: `ActionStep`,
  `ContainerInvocationError`, `FailureCode` (including `AUTHENTICATION_FAILED`,
  `AUTHORIZATION_FAILED`, `NOT_FOUND`, `OUTCOME_UNKNOWN`),
  `ExecutionContext.credential()`, `context.runtime_context`,
  `InstalledStepManifest` with credential targets — plus the runtime/OCI contract
  from the SDK's
  [docs/runtime-and-oci.md](https://github.com/syntara-orchestration/syntara-plugin-sdk/blob/devel/docs/runtime-and-oci.md).
  **Done: all exports verified in the a6 wheel — see the verification note.**
- [x] Record an `SDK_VERIFICATION.md`-style note that reconciles the EP cold-start
  Job/gRPC decision
  ([service-isolation-revisit-decisions](https://github.com/syntara-orchestration/syntara-execution-plane/blob/devel/docs/service-isolation-revisit-decisions.md))
  with the selected SDK runtime. Record workload ownership, actual
  invocation/result transport, lifecycle, limits, credential delivery, and cleanup.
  Select the process model there. A persistent gRPC service is allowed only when
  that note selects it.
  **Done: [aap-workflow-sdk-verification.md](aap-workflow-sdk-verification.md) —
  selects cold-start, single-invocation pods running the SDK synchronous gRPC
  ContainerService.**
- [ ] In the same note, record the approved OpenShift security context this image
  must run under: user, read-only root filesystem, dropped capabilities, writable
  emptyDir limits, and the SDK mechanism that delivers credentials. The story's
  security-context validation requirement ("verify startup and declared
  model/manifest/output-schema agreement ... under the approved OpenShift
  security context") requires this; user `65532` from `http-request` is a
  candidate, not the approval.
- [x] Surface the SDK publication blocker on the Jira story so release gating
  (AAP-98911) is not surprised later.
  **Done: comment posted to AAP-98907 (2026-11-23) — publication satisfied, pin
  `0.1.0a6`, runtime model selected.**

## Step 1 — Scaffold the package

- [x] Create `aap-workflow/` using the `http-request/` package layout:
  `pyproject.toml` (`syntara-node-aap-workflow`), `Containerfile`, `manifest.yaml`,
  `example.json`, `syntara-plugin.yaml`, `runtime-bindings.yaml`, `plugin.yaml`,
  `README.md`. The `__main__` entrypoint and any gRPC server follow the Step 0
  runtime note.
- [x] Containerfile installs the pinned SDK version from an index or prefetch
  input. It does not copy a local SDK checkout. Mark the base image provisional.
- [x] Add the step to the `Makefile`: `NODES`, `SDK_PLUGINS`, `PYTHON_PACKAGES`,
  and the per-node case arms (build/run filters).
- [x] Add provisional `.tekton` pipelines
  (`ao-aap-workflow-devel-{pull-request,push}.yaml`, `early-access` variants — rhel9 removed from pipeline file and SA names per PR #52 review)
  following the existing step pipelines. Label them as dev scaffolding so
  AAP-98910 can replace the build system when the release route is confirmed.

## Step 2 — Integration type and credentials

Match `resolve_aap_auth` in the current adapter. The integration owns the URL and
TLS material. The credential owns authentication. Neither is a free-form
`credential_name` on the step input; AO still maps today's `integration_id` and
`credential_id` in AAP-98908.

- [x] `syntara.aap` integration type with an `https` endpoint (AAP gateway base
  URL). Share the type with the Job adapter when AAP-98892 has already defined it.
  **Done: type defined with baseUrl/verifySsl/caCertificate; AAP-98892 has no
  SDK integration type yet (not in this repo) — sharing is tracked in open
  decision 5.**
- [x] Credential recipe accepts the shapes the current adapter accepts: bearer /
  OAuth token and basic auth. Carry TLS verify and CA certificate from the
  integration. Coordinate the exact recipe with AAP-95478.
  **Done: recipe matches `get_aap_auth_from_credentials` shapes (token wins over
  username+password); TLS verify/CA carried via runtime_context and validated
  (CA requires verification enabled). AAP-95478 coordination remains open
  before release.**
- [x] Decide whether an endpoint-credential destination guard (like
  `_credential_destination_matches` in the HTTP step) applies to the AAP base URL.
  **Done: the guard is inherent — the step never accepts a URL from input; all
  requests target `runtime_context["aapEndpoint"]`, validated (https, bare
  origin, no userinfo/query/fragment) before any credential access, mirroring
  the HTTP step's ordering.**
- [x] Do not log tokens, `extra_vars`, launch bodies, or AAP response bodies. Log
  status codes and safe body keys only, as the current launcher does. This is
  input to the AAP-98909 Product Security review.
  **Done: `_auth.py` logs endpoint host and auth mode only; test asserts
  credential values never appear in logs. Executor-level logging rules land
  with plan Step 4.**

## Step 3 — Models

Preserve the current author-facing surface. AO-only identifiers
(`integration_id`, `credential_id`) stay on the AO side; the container sees the
resolved endpoint and credential from Step 2.

- [x] Input, preserved from the current schema:
  - template by `workflow_job_template_id`, or by `workflow_job_template_name`
    plus organization (`organization_id` wins over `organization_name`)
  - `extra_vars`, `limit`, `scm_branch`, `tags` (sent to AAP as `job_tags`),
    `skip_tags`, `labels`
  - inventory override by `inventory_id` or by `inventory_name` (name requires
    organization)
  - `timeout` in seconds
- [x] Keep poll interval an implementation setting, as `aap_poll_interval_seconds`
  is today. Promoting it to an author input is a compatibility difference and
  needs an AAP-95478 record before it is added.
  **Done: no poll-interval input exists on the model or manifest.**
- [x] Output, preserved from the current result schema: `workflow_job_id`,
  `workflow_job_url`, parent `workflow_job_status`, `artifacts`, and `created` /
  `started` / `finished`. Failure results use the same fields and leave unknown
  ones empty; the failed branch also carries `error`. The result is a
  discriminated union on `status` (Completed/Failed) in the current schema —
  preserve that shape unless AAP-95478 approves a flatter contract. A
  child-job summary and an elapsed field are new aggregation; add either one
  only after open decision 1 is recorded and approved.
  **Done: union restored — `AapWorkflowOutput` carries a `status` discriminator
  with branch-required fields enforced by a model validator, and
  `manifest.yaml` output is the adapter's `oneOf` (Completed/Failed). No
  child-job summary or elapsed field added.**
- [x] `workflow_job_url`: the in-process adapter takes it from the AAP launch/job
  response. Record how the container obtains or constructs it (response field vs
  derived from the integration endpoint + job id) — AAP-98908 needs it for
  downstream projection.
  **Done: derived, not from the response — the adapter's `build_aap_job_url`
  formula is `{base_url}/execution/jobs/workflow/{job_id}/output`; the
  container replicates it in `_result.py` (verified against the adapter
  source).**
- [x] Bound the serialized result to the SDK 1 MiB limit. Truncate `artifacts`
  with a documented marker when they would exceed it. Do not drop the workflow
  identity fields to make room for artifact bytes.

## Step 4 — Executor

Use the gateway controller prefix the current adapter uses. The legacy
`/api/v2/` prefix is an open supported-version question (decision 2), not the
default path.

- [x] Resolve the template id (id wins over name + organization) and any inventory
  or label overrides, then launch:
  `POST {base_url}/api/controller/v2/workflow_job_templates/{id}/launch/`.
- [x] Observe only the parent:
  `GET {base_url}/api/controller/v2/workflow_jobs/{id}/`.
  The parent's terminal status is the step's terminal status. Do not infer
  completion from one child job. Do not poll `workflow_nodes` until open
  decision 1 says that projection is in scope. "Tolerated" child failure means
  the parent reached a success status under AAP's own node rules.
- [x] Retry policy, matching the current client:
  - Connection loss or a retryable HTTP status **before** the launch POST is
    sent → `UPSTREAM_TRANSIENT`, retryable.
  - Anything after the POST has been sent follows Step 5. Do not map that loss
    to a retryable launch.
  - Launch HTTP 500 → non-retryable. A 500 on launch may be a real rejection.
    A 500 on a later status GET is transient and stays inside the poll loop.
  - Poll-time 429, 500, 502, 503, 504, and connection errors retry inside the
    poll loop with a bounded consecutive-error cap. They do not relaunch.
- [x] Failure mapping for a launch that did not produce a job id:
  - 401 → `AUTHENTICATION_FAILED`
  - 403 → `AUTHORIZATION_FAILED`
  - missing template → `NOT_FOUND`
  - other launch rejection → `REMOTE_REJECTED`
  - deadline with no job id → `TIMEOUT`
  All of these are non-retryable once a request was sent (Step 5).
- [x] A parent that reaches a failure status (`failed`, `error`, `canceled` from
  AAP) is a completed observation. Return the preserved output fields and a
  non-retryable outcome (`not_completed`). Do not relaunch. Record, for
  AAP-98908, whether that is a successful SDK result carrying
  `workflow_job_status` or a `ContainerInvocationError` whose detail still
  carries the same fields. AO's current activity does the latter: it fails the
  node and still returns the output.
- [x] Cancellation: on `context.cancellation`, best-effort
  `POST {base_url}/api/controller/v2/workflow_jobs/{id}/cancel/`, then
  `CANCELLED`. The outcome says cancel was requested. It does not say child
  jobs have stopped, and a failed cancel POST does not change that. Workload
  cleanup does not establish the workflow's terminal state. Skip the AAP call
  when no job id is known.

**Step 4 implemented in `executor.py`** (20 executor tests + run-level tests,
129 total passing): gateway prefix `/api/controller/v2/`; template/inventory
name lookups require exactly one match; labels resolved and created with the
adapter's security guards (max 100, pattern, length, null-byte); launch body
matches `_LAUNCH_BODY_FIELDS` (`tags` → `job_tags`, truthiness skips);
`ConnectError` before send → retryable `UPSTREAM_TRANSIENT` (bounded internal
retries on pre-launch reads); any other launch-phase transport loss →
`OUTCOME_UNKNOWN` (never re-POSTs; correlation-token reconciliation is Step 5);
launch 429/502/503/504 with a received response → non-retryable
`REMOTE_REJECTED`; poll loop retries 429/500/502/503/504 + connection errors
with a 5-consecutive-error cap → `OUTCOME_UNKNOWN`; healthy polls past the
deadline → `TIMEOUT`; cancellation does a best-effort AAP cancel then
`CANCELLED`, and a failed cancel POST does not change the outcome.
**Recorded decision for AAP-98908:** a parent failure status returns the
failed branch of the result union as a successful SDK result — the SDK ABI
cannot attach the preserved output fields to a `StepFailure` — so AO maps
`status == "failed"` to node failure. This mirrors the adapter's result
schema, which carries the Failed branch in the result namespace.

## Step 5 — Uncertain launch and uncertain completion

Epic AC 4 covers both. Adopting "any running job for this template" can bind
another author's run, and retrying a lost POST can start a second workflow.

- [x] Before `POST .../launch/`, attach a correlation token this attempt owns
  (a unique label or `extra_var`). Persist it with the attempt.
- [x] If the launch response is lost, look up workflow jobs for the template
  that carry that token, in pre-terminal and running states (`new`, `pending`,
  `waiting`, `running`), not only `running`.
- [x] If one match exists, adopt that id and poll. Do not POST again.
- [x] If none exists, one further launch is allowed, preceded by the same
  lookup. A second lost response repeats the lookup before any further POST.
- [x] Once a job id is known, launch is never retried. Further failures return
  that id.
- [x] Uncertain completion is separate from timeout. If poll errors continue
  until the deadline, return `OUTCOME_UNKNOWN` (outcome `unknown`) with the
  known id and a "launched but lost contact" message. If polls succeed and the
  parent is still non-terminal at the deadline, return `TIMEOUT` with the known
  id. Neither path launches again.


**Step 5 implemented:** each launch attempt owns a `uuid4` correlation token
carried in the reserved `extra_vars` key `_syntara_launch_token` (inputs
containing that key are rejected). A lost launch response triggers a lookup of
workflow jobs for the template (`GET .../workflow_job_templates/{id}/jobs/`)
matching the token in pre-terminal states (`new`, `pending`, `waiting`,
`running`) — never status alone. One match is adopted and polled without a
second POST; no match allows at most one further launch (second POST),
preceded by the same lookup; a second loss with no match is terminal
`OUTCOME_UNKNOWN`. Ambiguous or failed lookups are `OUTCOME_UNKNOWN`. Once a
job id is known the launch is never retried. Uncertain completion was already
implemented in Step 4 (deadline-with-errors → `OUTCOME_UNKNOWN`; healthy
non-terminal at deadline → `TIMEOUT`).

**Compatibility difference to record for AAP-95478:** the correlation token
is visible in the launched workflow job's `extra_vars` under the reserved
underscore-prefixed key. A template that enumerates extra_vars would see it;
it does not affect templates that read specific keys.
## Step 6 — Tests

- [x] Unit tests against a fake AAP API (extend the `http_upstream.py` fixture
  pattern).
- [x] Cases from the story validation list, with the contracts above:
  - Preserved inputs (including organization, inventory, `scm_branch`,
    `skip_tags`, labels) and credentials (token and basic auth); parent
    identity, URL, and status
  - Successful child execution reflected as parent success, plus artifacts
  - Parent failure, and a tolerated child failure where the parent still succeeds
  - Parent success is not inferred from a single successful child while the
    parent is non-terminal
  - Launch rejection; missing template; 401; 403
  - Connection loss before send (retryable) and after send (reconcile, no second
    launch)
  - Timeout with a known id; lost poll contact (`OUTCOME_UNKNOWN`) with a known id
  - Cancellation with active children: cancel is requested, children are not
    reported stopped, and a failed cancel POST still returns `CANCELLED`
  - Uncertain launch adopts the correlated job and does not launch a second one
  - Startup and declared model/manifest/output-schema agreement outside the SDK
    repository, under the security context recorded in Step 0
- [x] Serialized result stays within 1 MiB, and artifact truncation keeps
  `workflow_job_id`.
  **Done: `bound_result_size` in `_result.py` drops largest artifact keys first,
  records them under `__syntara_truncated_artifacts__`, never drops identity
  fields, and fails terminally if the result still exceeds the limit without
  artifacts.**
- [x] Logs for a launch that includes `extra_vars` and a token do not contain
  those values or the AAP response body.


**Step 6 implemented:** unit tests use a fake AAP gateway via respx (30 tests:
auth/endpoint/TLS validation, launch-body parity, rejection mapping, pre/post-
send loss, transient poll retries, consecutive-error cap, timeout vs lost
contact, cancellation with and without a successful cancel, correlation-token
adoption, label creation, serialized-result schema parity). Integration tests
(`tests/integration/test_aap_workflow_grpc.py`, 12 tests) exercise the built
image through its gRPC front door against a controlled fake AAP gateway over
TLS: success with artifacts and manifest output-key agreement, failed parent,
name lookup, missing template, 401, missing endpoint, bad credential,
uncertain-launch adoption with exactly one POST, cancellation with a
best-effort AAP cancel, timeout with a known job id, and container-log leak
checks for the token and extra_vars. Full suite: 139 unit + 39 integration
tests pass. **Open:** the "under the approved OpenShift security context"
clause remains tied to open decision 7 (context is still a candidate, not
approved).
## Step 7 — Documentation

- [x] Step README: usage, preserved inputs/outputs, failure and cancellation
  behavior, prerequisites (reachable supported AAP, a workflow job template with
  at least one child job, authorized integration credentials).
- [x] Compatibility record against the in-process step: every intentional
  difference, including anything still withheld under the open decisions.
  AAP-98908 needs this to project today's result schema.
- [x] The Step 0 runtime/transport note, the security context, and the logging
  rules. Hand those to AAP-98909; this story does not close the Product
  Security review.


**Step 7 implemented:** `aap-workflow/README.md` completed (prerequisites,
inputs/outputs, failure-behavior table, cancellation behavior); compatibility
record against the in-process step (11
intentional differences + withheld items, addressed to AAP-98908); AAP-98909
review inputs section (runtime/transport, security-context candidate, logging
rules, credential handling). The story does not close the Product Security
review.
## Open decisions (track, do not assume)

1. Which child-result/artifact aggregation and nested or approval behavior is
   supported. Until a decision is recorded, poll the parent only and do not add
   a child-job summary. Existing `artifacts` on the parent job stay.
2. How parent/child cancellation, reconciliation, credentials, and supported
   AAP/API versions affect the container boundary. Until that is recorded, use
   `/api/controller/v2/` on the integration base URL and the current token plus
   basic-auth shapes. Legacy `/api/v2/` is not the default.
3. Operator responsibilities during install/upgrade (parent epic / AAP-98912).
4. Image version and upgrade policy (parent epic / AAP-98912).
5. Where shared AAP client helpers live, given the Job adapter (AAP-98892) is a
   separate repo/epic. Share `syntara.aap` if that epic already defined it.
6. Active SDK PyPI publication tracking (ANSTRAT-2422; AAP-95948 was a historical
   duplicate, not evidence of satisfaction).
7. Approved OpenShift security context and writable-storage layout (story AC 7).
   Record it in Step 0 from the approved platform design.
8. Release route, base image, and build system (AAP-98910 / AAP-98911, with
   context from AAP-90779, AAP-97807, AAP-98587). Dev scaffolding may mirror
   existing steps; it does not decide the released image.

## Dependencies

- [ANSTRAT-2422](https://redhat.atlassian.net/browse/ANSTRAT-2422) — published SDK
  runtime/contracts (blocker for release, not development). Related SDK scope:
  AAP-95400, AAP-95402, AAP-95408.
- [AAP-95478](https://redhat.atlassian.net/browse/AAP-95478) — compatibility rules
- [AAP-95472](https://redhat.atlassian.net/browse/AAP-95472) — Step Type Alignment
  and Refactoring Governance (Jira "depends on" link)
- [AAP-98873](https://redhat.atlassian.net/browse/AAP-98873) — shared AO OpenShift
  connection/invocation work
- [AAP-98892](https://redhat.atlassian.net/browse/AAP-98892) — related AAP Job
  adapter epic (shared helpers, separate images)
