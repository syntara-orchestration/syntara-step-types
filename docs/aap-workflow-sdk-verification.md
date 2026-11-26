# AAP Workflow step — SDK verification and runtime reconciliation (Step 0)

Story: [AAP-98907](https://redhat.atlassian.net/browse/AAP-98907) ·
Plan: [aap-workflow-plan.md](aap-workflow-plan.md) · Recorded: 2026-11-23

## 1. SDK publication status (ANSTRAT-2422)

Both packages are **published on PyPI** — the publication blocker is satisfied for
development; confirm the SDK team's active tracking ticket records this (AAP-95948
was a historical duplicate, not evidence).

| Package | Latest on PyPI | Uploaded | Notes |
| --- | --- | --- | --- |
| `syntara-plugin-contracts` | `0.1.0a6` | 2026-11-17 | a1–a6 available |
| `syntara-plugin-sdk` | `0.1.0a6` | 2026-11-17 | a1–a6 available |

Current repo state before this step: `uv.lock` pins SDK `0.1.0a5`; the local venv
had `0.1.0a4` installed (stale); `http-request/Containerfile` pins
`CONTRACTS_VERSION=0.1.0a5`; `.sdk-dist/` holds `0.1.0a2` wheels (stale, not the
image dependency path — see the repo `SDK_VERIFICATION.md`).

**Agreed version: `0.1.0a6`.** Development, the image install, and the release all
resolve this pin from PyPI. A local checkout/wheel is a development convenience
only.

### a5 → a6 delta (backwards compatible)

Verified by diffing the wheels: only `runtime/container.py` and
`container.proto` change. The container runtime now enforces the execution budget
itself — when `timeout_seconds` expires it signals cancellation and emits
`TIMEOUT`/`not_completed` (retryable), holding a 5-second terminal-event delivery
grace. The proto comment clarifies `timeout_seconds` is an execution budget
excluding terminal-event delivery, and does not prove arbitrary handler code has
stopped. No API removals or signature changes.

**Consequence for the Workflow step:** the SDK enforces the deadline at the ABI
level. The executor's own poll-deadline handling must return its failure *before*
the budget expires so the failure carries the workflow job id and a precise
outcome (see plan Step 4/5: `TIMEOUT` vs `OUTCOME_UNKNOWN`). The step's declared
`timeout_seconds` input maps to the invocation budget.

## 2. Verified contract surface (`0.1.0a6`)

All APIs the Workflow step needs are exported from `syntara_plugin.runtime`
(91 exports):

- `ActionStep`, `StepFailure`, `ErrorCategory`
- `ContainerInvocationError`, `FailureCode` — full member set confirmed:
  `INVALID_INPUT`, `AUTHENTICATION_FAILED`, `AUTHORIZATION_FAILED`,
  `REMOTE_REJECTED`, `NOT_FOUND`, `CONFLICT`, `RATE_LIMITED`,
  `UPSTREAM_TRANSIENT`, `TIMEOUT`, `CANCELLED`, `POLICY_DENIED`, `ABI_VIOLATION`,
  `OUTCOME_UNKNOWN`, `CREDENTIAL_SCAN_LIMIT` (all four codes the plan's failure
  mapping needs are present)
- `ExecutionContext` with `runtime_context: Mapping[str, Any]`,
  `cancellation: Event`, and `credential(name) -> CredentialBinding`
  (invocation-scoped, non-serializable, `.read()` returns a detached document)
- `InstalledStepManifest` (with credential targets), `StepBindings`,
  `StepDescriptor`, `add_container_runtime_service`,
  `step_registry_from_bindings`
- `FileCredentialProvider`, `CredentialResolver` and related credential machinery

Limits confirmed in the runtime: 1 MiB serialized result (`_MAX_JSON_BYTES`),
max timeout 86,400 s, credential documents bounded to 1 MiB with a 10,000-node
traversal cap, and result/error/log leak scanning for credential values.

## 3. Runtime model reconciliation (EP cold-start decision vs SDK runtime)

Reconciles the EP
[service-isolation-revisit-decisions](https://github.com/syntara-orchestration/syntara-execution-plane/blob/devel/docs/service-isolation-revisit-decisions.md)
with the SDK
[runtime-and-oci.md](https://github.com/syntara-orchestration/syntara-plugin-sdk/blob/devel/docs/runtime-and-oci.md):

**No conflict exists.** The two documents govern different layers:

- The **SDK** supports exactly one container model: the gRPC `ContainerService`
  implementing `syntara.container/v1alpha1` (synchronous variant, ≤16 concurrent
  invocations, or asynchronous variant, 8 daemon workers). It has no cold-start
  job model of its own — it is the in-pod ABI.
- The **EP decision** chooses cold-start Kubernetes/OpenShift Jobs: one
  `batch/v1` Job per invocation, `backoffLimit: 0`, TTL cleanup, "accepts one
  invocation and then exits". gRPC over loopback (API port-forward) is the sole
  application channel, carrying inputs, progress, errors, results, logs — never
  Secrets, pod logs, `exec`, or stdin.

**Selected process model for the AAP Workflow step:** a cold-start container
(one invocation per pod) that starts the SDK synchronous gRPC `ContainerService`
on `0.0.0.0:50051` (the container's loopback as seen by the launcher), handles one
Execute plus its control RPCs, and exits. This is the `http-request` entrypoint
pattern — adopted here as the *recorded selection*, not an unexamined copy.

Recorded answers to the story's required items:

- **Workload ownership:** AO owns user integration and Temporal state; EP owns
  execution copies and work state; the container owns only the AAP workflow
  launch/observation attempt. Cleanup of the step workload does not establish the
  AAP workflow's terminal state (best-effort AAP cancel remains in the executor).
- **Invocation/result transport:** versioned gRPC SDK protocol over loopback
  port-forward. No persistent gRPC service, no execution-plane agent, no
  replacement dispatch model. The synchronous runtime (bounded concurrency) is
  sufficient: a cold-start pod serves one invocation.
- **Lifecycle:** launcher starts the Job, connects, invokes, receives one
  terminal event, then the Job is torn down (TTL). Result persistence precedes
  Job cleanup. EP requeues only failures with positive evidence Execute was not
  submitted; ambiguous outcomes land in `reconciliation_required` — which is why
  the executor must prefer precise outcomes (`OUTCOME_UNKNOWN` with the known
  job id) over ambiguous ones, per plan Step 5.
- **Limits:** 1 MiB serialized result (artifact truncation per plan Step 3);
  execution budget enforced by the SDK (§1); credential documents 1 MiB.
- **Credential delivery:** SDK callback mechanism — credential names resolve
  within the current attempt and return bounded JSON; credential IDs/references
  are never sent to the workload; results are scanned for leaks. The step reads
  credentials via `context.credential(name).read()` only.
- **Cancellation:** cooperative gRPC `Cancel` mapped to
  `context.cancellation`; Job deletion is the forced-termination fallback and is
  the launcher's responsibility, not the step's.

Open item carried to AAP-98873/AAP-98908: the exact mTLS bootstrap
(`SYNTARA_RUNTIME_GRPC_TLS_CERT`/`_KEY`/`_CLIENT_CA`, `SYNTARA_RUNTIME_CONFIG`)
and port-forward wiring on the AO side. The SDK requires mutual TLS between
launcher and workload; this repo's existing steps run `add_insecure_port` in
dev/test entrypoints — the production entrypoint must follow the SDK's TLS
contract once the invocation story fixes the launcher details.

## 4. OpenShift security context (candidate, pending approval)

The story requires verification "under the approved OpenShift security context."
No approval exists yet (plan open decision 7). Candidate, mirroring
`http-request` and SDK requirements:

- run as nonroot user `65532:65532` (no root escalation)
- read-only root filesystem; writable emptyDir only where the runtime needs it
  (TLS material mounts, registration manifest is read-only at
  `/opt/syntara/registration-manifest.json`)
- all capabilities dropped; `allowPrivilegeEscalation: false`
- no secrets as environment variables — credentials arrive via the SDK callback
  channel

This section is input to AAP-98909 (Product Security review); it is not the
approval.

## 5. Actions resulting from this note

- [x] Publication confirmed on PyPI (`0.1.0a6`, 2026-11-17) — record on the Jira
  story that the ANSTRAT-2422 release blocker is satisfied for development.
- [x] Contract surface verified against `0.1.0a6`.
- [ ] Bump `uv.lock` and dev venv to `0.1.0a6`; the `aap-workflow` image installs
  the hashed `requirements.txt` export of that lock (existing steps' pins are
  updated when touched or in a coordinated bump).
- [ ] Entry point and gRPC server scaffold follow §3 (cold-start,
  single-invocation, synchronous runtime).
- [ ] TLS production entrypoint detail tracked with AAP-98873/AAP-98908 (§3 open
  item).
- [ ] Security context approval tracked as plan open decision 7 / AAP-98909.
