# AAP Workflow container execution — architecture and security boundaries

Story: [AAP-98909](https://redhat.atlassian.net/browse/AAP-98909) ·
Epic: [AAP-98893](https://redhat.atlassian.net/browse/AAP-98893) ·
Recorded: 2026-11-27

Status: **draft for Product Security review.** This document consolidates the
architecture and security boundaries for the AAP Workflow container step and is
the review input for AAP-98909. It is not the approval. Sections marked
*unresolved* must remain explicit until a linked decision is recorded; this
document does not decide them.

Companion material:

- Implementation: [`aap-workflow/`](../aap-workflow/) in this repository
  (branch `ana/aap-98907-aap-workflow`)
- AO dispatch: `syntara` repository (branch `feat/aap-98907-aap-workflow-ep`)
- Runtime selection and SDK verification:
  [aap-workflow-sdk-verification.md](aap-workflow-sdk-verification.md)
- Implementation plan and compatibility record:
  [aap-workflow-plan.md](aap-workflow-plan.md)
- Shared SDK/EP design (referenced, not duplicated):
  [EP service-isolation-revisit-decisions](https://github.com/syntara-orchestration/syntara-execution-plane/blob/devel/docs/service-isolation-revisit-decisions.md)
  and the SDK
  [runtime-and-oci contract](https://github.com/syntara-orchestration/syntara-plugin-sdk/blob/devel/docs/runtime-and-oci.md)

## 1. System context and responsibilities

The AAP Workflow step launches and observes a workflow job template on a
customer-operated AAP instance. AAP owns the underlying workflow and its child
jobs; the step owns only the launch/observation attempt.

| Layer | Owns | Does not own |
| --- | --- | --- |
| AO (control plane) | User integration config, workflow definition, Temporal state, node output projection, audit events | AAP workflow/child-job state |
| Execution Plane (EP) | Work state, cold-start Job lifecycle, mTLS command/callback bridge, workload cleanup | AAP workflow terminal state |
| AAP Workflow container | Template resolution, launch, parent observation, result projection, best-effort cancel | Child-job lifecycle, cluster operations |
| AAP (customer) | Workflow job, child jobs, node rules, terminal statuses | — |

**Boundary rule (epic AC):** step workload cleanup alone does not establish the
AAP workflow's terminal state. A best-effort cancel is requested; nothing more
is claimed.

## 2. Invocation and result transport

Recorded in [aap-workflow-sdk-verification.md §3](aap-workflow-sdk-verification.md)
and reconciled with the EP cold-start decision — no conflict exists between the
SDK container ABI and the EP Job model; they govern different layers.

1. AO's dispatch activity validates node parameters, resolves the AAP
   integration, and submits a one-shot `WorkExecute` command to the EP with a
   deterministic `work_item_id`, the step revision, workload inputs, credential
   bindings, and a runtime context carrying `aapEndpoint`, `aapVerifySsl`, and
   optional `aapCaCertificate`.
2. The EP starts a cold-start OpenShift Job (one pod per invocation,
   `backoffLimit: 0`, TTL cleanup) running the step image.
3. The container serves the SDK **synchronous gRPC `ContainerService`** over
   loopback (port-forward). No persistent gRPC service, no execution-plane
   agent, no replacement dispatch model. Mutual TLS between launcher and
   workload per the SDK TLS environment contract (dev/test uses an insecure
   port; production mTLS wiring is the open item tracked with AAP-98873).
4. One `Execute` plus its control RPCs (`Cancel`, `Health`) are served; the
   container then exits.
5. The result returns through the EP completion path: either read directly if
   the work item is already terminal, or delivered asynchronously as a
   `work.execute.completed` callback over the authenticated mTLS bridge. AO
   validates the result against the plugin output schema (malformed terminal
   data → protocol error) and completes the Temporal activity.
6. Ambiguous submissions land in `reconciliation_required` — which is why the
   executor prefers precise outcomes (`OUTCOME_UNKNOWN` with a known job id)
   over ambiguous ones.

The node contract is unchanged from the in-process executor: same inputs,
outputs, and error handling; only the executor location differs. The dispatch
path is gated behind the `workflow_engine.aap_workflow_ep_dispatch_enabled`
runtime setting (default false) until the operator/version decisions are
recorded.

## 3. Workflow-template/API compatibility

- Gateway controller API: `{aapEndpoint}/api/controller/v2/`. The legacy
  `/api/v2/` prefix is a supported-version question, not the default path
  (*unresolved* — plan open decision 2).
- Template resolution: by id, or by name + organization (id and
  `organization_id` win over their name counterparts; name lookups require
  exactly one match).
- Launch: `POST .../workflow_job_templates/{id}/launch/` with the adapter's
  launch-body fields (`tags` sent as `job_tags`, truthiness skips).
- Observation: `GET .../workflow_jobs/{id}/` — **parent only** (see §4).
- Cancellation: best-effort `POST .../workflow_jobs/{id}/cancel/`.
- Credentials: token (Bearer) or username+password (basic); token wins, matching
  the in-process adapter's `resolve_aap_auth`.
- Author-facing input/output schemas preserve the in-process adapter's
  contracts, including the discriminated output union on `status`
  (Completed/Failed). The compatibility record with every intentional
  difference is tracked against AAP-98907/AAP-95478 (11 intentional differences,
  summarized in the [compatibility matrix](compatibility-matrix.md)).

## 4. Parent/child state ownership and aggregation (*partially unresolved*)

The parent workflow job's terminal status **is** the step's terminal status.

- The step polls only the parent workflow job. Completion is never inferred
  from a single child job. "Tolerated" child failure means the parent reached a
  success status under AAP's own node rules.
- Existing `artifacts` on the parent workflow job are projected. No child-job
  enumeration (`workflow_nodes`), per-child status, stdout retrieval, or
  child-result aggregation is implemented.
- **Unresolved (epic open question 3 / plan open decision 1):** which
  child-result/artifact aggregation and nested or approval behavior is
  supported. Until a decision is recorded, parent-only observation stands, and
  no child-job summary or elapsed field is added. Per-launch (child) credentials
  are likewise withheld pending the same decision.

Artifact exposure and limits: the serialized result is bounded to the SDK's
1 MiB limit. `artifacts` is truncated first — largest keys dropped and listed
under `__syntara_truncated_artifacts__` — and identity fields
(`workflow_job_id`, `workflow_job_url`, `workflow_job_status`) are never
dropped to make room. A result that still exceeds the limit with no artifacts
left fails terminally.

## 5. Credential delivery

Credentials never appear as step input and never transit author-controlled
fields.

1. AO resolves the node's AAP credential and passes a **credential binding**
   (recipe `syntara.aap.credential`) with the invocation — never the secret.
2. The EP worker calls back to AO's `credential.resolve.workload` command to
   resolve the document (mTLS, CN-checked). Empty injector values are dropped.
3. The container receives the bounded document (≤1 MiB, traversal-capped)
   through the SDK credential callback and reads it via
   `context.credential("syntara.aap.credential").read()`.
4. The integration endpoint and TLS material arrive via `runtime_context`
   (`aapEndpoint`, `aapVerifySsl`, `aapCaCertificate`) and are **validated
   before any credential access**: HTTPS only, bare origin, no
   userinfo/query/fragment. All AAP requests target that endpoint only, so
   credentialed calls cannot be redirected by author input.
5. The SDK runtime scans results, errors, and logs for credential values;
   outputs containing them are rejected. The container-log leak check is
   enforced by test.

Credential recipe shape (token, or username+password) matches the in-process
adapter; the final recipe is *provisional* pending AAP-95478 coordination.

## 6. Launch correlation

Uncertain launches must not produce duplicate workflows or bind another
author's run (epic AC 4):

- Each launch attempt owns a `uuid4` correlation token carried in the reserved
  `extra_vars` key `_syntara_launch_token`. Inputs containing that key are
  rejected (`INVALID_INPUT`).
- If the launch response is lost, the executor looks up workflow jobs for the
  template carrying the token in **pre-terminal states** (`new`, `pending`,
  `waiting`, `running`) — never "any running job". One match is adopted and
  polled; no second POST. No match allows at most one further launch, preceded
  by the same lookup. Ambiguous or failed lookups are `OUTCOME_UNKNOWN`.
- Once a job id is known, launch is never retried; all subsequent failures
  carry that id.
- **Compatibility difference recorded for AAP-95478:** the token is visible in
  the launched job's `extra_vars` under the reserved underscore-prefixed key.

## 7. Timeout and cancellation propagation

Timeouts:

- The step's `timeout` input maps to the invocation's execution budget. Since
  SDK `0.1.0a6` the runtime **enforces the budget at the ABI level** (signals
  cancellation, emits retryable `TIMEOUT`/`not_completed` with a 5-second
  terminal-delivery grace). The executor's own poll deadline fires *before* the
  budget expires so the failure carries the workflow job id and a precise
  outcome.
- Healthy polls past the deadline → `TIMEOUT` with the known job id. Poll
  contact lost (5 consecutive errors, or errors to the deadline) →
  `OUTCOME_UNKNOWN` ("launched but lost contact") with the known job id.
  Neither path launches again.

Cancellation:

- AO cancels dispatched nodes by bulk-cancelling their deterministic work-item
  ids on the EP (best-effort, batched); acknowledgment ≠ Kubernetes cleanup
  finished.
- The EP delivers cooperative gRPC `Cancel` to the container, mapped to
  `context.cancellation` (a threading `Event` checked before launch and inside
  the poll loop). Job deletion is the forced-termination fallback and is the
  launcher's responsibility.
- Inside the container: with no known job id, report `CANCELLED` without
  contacting AAP; with a known id, best-effort `POST .../cancel/` then
  `CANCELLED`. The outcome says cancel was **requested** — it does not claim
  the workflow or child jobs have stopped, and a failed cancel POST does not
  change the outcome.

## 8. Uncertain external outcomes

Failure codes and retryability (full table in the step
[README](../aap-workflow/README.md#failure-behavior)); the security-relevant
property is that **nothing uncertain is retried by re-launching**:

| Situation | Outcome | Retryable |
| --- | --- | --- |
| Connection never established before launch POST | `UPSTREAM_TRANSIENT` | yes (bounded internal retries) |
| Launch response lost | `OUTCOME_UNKNOWN` + token reconciliation (§6) | no |
| Launch 401/403/404 | `AUTHENTICATION_FAILED` / `AUTHORIZATION_FAILED` / `NOT_FOUND` | no |
| Other launch rejection (incl. 500; 429/502/503/504 with a received response) | `REMOTE_REJECTED` | no |
| Missing/misconfigured integration or credential | `POLICY_DENIED` | no |
| Deadline, parent non-terminal | `TIMEOUT` (known id) | no |
| Lost poll contact | `OUTCOME_UNKNOWN` (known id) | no |
| Parent reaches `failed`/`error`/`canceled` | failed branch of the result union | — |

A parent failure status is a completed observation returned as the failed
branch of the result union (the SDK ABI cannot attach preserved output fields
to a `StepFailure`); AO maps `status == "failed"` to node failure.

## 9. Customer prerequisites and cluster boundaries

Shared with [AAP-98873](https://redhat.atlassian.net/browse/AAP-98873); the
customer provides and configures the OpenShift cluster.

- A reachable, supported AAP instance (gateway controller API over HTTPS) and
  authorized credentials (token, or username+password).
- A workflow job template containing at least one child job.
- An AO AAP integration configured with the gateway base URL (and optional TLS
  verify/CA settings).
- The AO↔EP boundary is the existing `WorkExecute` command path plus the
  `syntara.aap` integration projection; AO does not create OpenShift
  Pods/Jobs itself. Step images are referenced per-work via the registry
  (`RELATED_IMAGE_AAP_WORKFLOW` override available).

**Unresolved — operator responsibilities (epic open question 1, AAP-98912):**
whether the operator registers step definitions and image references in AO,
deploys or pre-pulls images into the customer cluster, or both.

**Unresolved — image version and upgrade policy (epic open question 2,
AAP-98912):** version selection/pinning/update/retention, in-flight executions
during upgrade, and rollback behavior.

## 10. OpenShift security context (candidate — input to review, not approval)

Mirrors `http-request` and the SDK requirements; **pending Product Security
approval** (plan open decision 7):

- Run as nonroot user `65532:65532`; no root escalation.
- Read-only root filesystem; writable emptyDir only where the runtime needs it
  (TLS material mounts); registration manifest read-only at
  `/opt/syntara/registration-manifest.json`.
- All capabilities dropped; `allowPrivilegeEscalation: false`.
- No secrets as environment variables — credentials arrive only via the SDK
  callback channel (§5).
- gRPC over loopback port-forward only: never Secrets, pod logs, `exec`, or
  stdin. Mutual TLS between launcher and workload in production.
- Image installs pinned, hashed dependencies only (`pip --require-hashes` from
  the `uv.lock` export; SDK `syntara-plugin-contracts==0.1.0a6` from PyPI).
  Base image is provisional pending AAP-98910/AAP-98911.

Logging rules (implemented, test-enforced): tokens, `extra_vars`, launch
bodies, and AAP response bodies are never logged; endpoint host and auth mode
only. The SDK runtime additionally scans results, errors, and logs for
credential values.

## 11. Trace evidence against the documented call chain

The story's validation traces, with current implementation evidence:

| Trace | Status | Evidence |
| --- | --- | --- |
| Workflow success with a child job | covered | unit `test_successful_launch_and_completion`; image `test_successful_workflow` (artifacts + manifest output-key agreement) |
| Failing child (parent failure) | covered | `test_parent_failure_status_returns_failed_branch`; image `test_failed_parent_returns_failed_branch` |
| Tolerated child failure (parent still succeeds) | **partial** — only via parent status; not distinguishable without child observation (§4) | deferred with open decision 1 |
| Launch rejection | covered | `test_launch_rejection_mapping`; image auth/missing-template cases |
| Lost connection after launch | covered | `test_lost_launch_response_is_outcome_unknown`, `test_lost_launch_adopts_correlated_job` (+7 more) |
| Cancellation with active children | covered at step level (cancel requested, children not reported stopped, failed cancel POST still `CANCELLED`); dispatched-path cancellation covered only generically | `test_cancellation_*`; AO `test_work_cancellation.py` |
| Sensitive artifact exposure review | partial — 1 MiB truncation + leak scans + log rules implemented; formal security review **not yet obtained** | §5, §10; image `test_container_logs_do_not_leak_the_credential` |

**Not yet exercised end-to-end:** a dispatched AAP workflow node through the
real AO→EP→container path (the only E2E today covers script dispatch), and
startup under the *approved* security context (approval pending).

## 12. Actions requested from Product Security review

1. Approve or amend the OpenShift security context candidate (§10).
2. Confirm the credential delivery chain (§5) — runtime-context endpoint
   validation ordering, callback-only secret resolution, leak scanning — is
   acceptable, including the visible `_syntara_launch_token` extra-var.
3. Confirm the artifact exposure and truncation behavior (§4) is acceptable.
4. Confirm the mTLS bootstrap approach (§2, open item with AAP-98873) and the
   interim dev/test insecure port.

Release-blocking actions from the review are to be tracked to closure through
this story; unresolved epic questions (operator responsibilities, image
version/upgrade policy, child aggregation scope) remain with AAP-98912 and the
parent epic and are not decided here.
