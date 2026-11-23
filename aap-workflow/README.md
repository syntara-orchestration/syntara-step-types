# aap-workflow step

Launches and observes a workflow job template on an existing Ansible Automation
Platform (AAP) instance from a container step. AAP owns the underlying workflow
and its child jobs; the step launches the template, observes the parent workflow
job to a terminal status, and projects the preserved result fields.

**Status: implemented and tested (plan Steps 1–7).** Input/output contracts,
integration type, credential recipe, gRPC entrypoint, the launch/observe
executor, uncertain-launch correlation-token reconciliation, unit and
integration suites, and this documentation are in place. The compatibility
record against the in-process step is tracked on
[AAP-98907](https://redhat.atlassian.net/browse/AAP-98907) and summarized in
the repository's
[compatibility matrix](../docs/compatibility-matrix.md); also see
[docs/aap-workflow-plan.md](../docs/aap-workflow-plan.md) and
[docs/aap-workflow-sdk-verification.md](../docs/aap-workflow-sdk-verification.md).

## Prerequisites

- A reachable, supported AAP instance (gateway controller API over HTTPS).
- A workflow job template containing at least one child job.
- Authorized AAP integration credentials (token, or username + password).
- An AO AAP integration configured with the gateway base URL (and optional
  TLS verify/CA settings), resolved by AO into the step's runtime context.

## Runtime model

Cold-start, single-invocation pods running the SDK synchronous gRPC
`ContainerService` on `0.0.0.0:50051` — the recorded selection in the SDK
verification note. Dev/test entrypoints use an insecure port; the production
launcher wires mutual TLS through the SDK's TLS environment contract.

## Inputs

Preserves the in-process adapter's author-facing schema. AO-only identifiers
(`integration_id`, `credential_id`) are resolved by AO before invocation; the
container receives the resolved endpoint and credential.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `workflow_job_template_id` | integer | one of id/name | Template ID (takes precedence over name) |
| `workflow_job_template_name` | string | one of id/name | Template name (requires `organization_name`) |
| `organization_id` | integer | no | Organization ID (takes precedence over name) |
| `organization_name` | string | with name lookup | Organization name |
| `extra_vars` | object | no | Extra variables for the workflow |
| `limit` | string | no | Limit execution to specific hosts |
| `scm_branch` | string | no | Source control branch override |
| `tags` | string | no | Tags to run (comma-separated; sent as `job_tags`) |
| `skip_tags` | string | no | Tags to skip (comma-separated) |
| `labels` | array of strings | no | AAP label names to append |
| `inventory_id` | integer | no | Inventory override by ID |
| `inventory_name` | string | no | Inventory override by name (requires `organization_name`) |
| `timeout` | integer | no | Execution budget in seconds |

The integration endpoint, TLS material, and credential are **not author
inputs**. AO resolves the AAP integration and credential and supplies them
through the SDK runtime: `runtime_context["aapEndpoint"]` (HTTPS gateway base
URL), `runtime_context["aapVerifySsl"]`, optional
`runtime_context["aapCaCertificate"]`, and the declared `syntara.aap.credential`
document (token, or username + password — token wins, matching the in-process
adapter's `resolve_aap_auth`). The step validates the endpoint and TLS settings
before touching the credential; all AAP requests target that endpoint only.

## Outputs

Preserves the in-process adapter's discriminated union on `status`
(Completed/Failed):

| Field | Type | Description |
| --- | --- | --- |
| `status` | `"completed"` \| `"failed"` | Result branch |
| `workflow_job_id` | integer | AAP workflow job ID (required on both branches) |
| `workflow_job_url` | string | URL to view the workflow job in the AAP UI — derived from the integration endpoint as `{base_url}/execution/jobs/workflow/{job_id}/output`, the same formula as the in-process adapter (not taken from the AAP response) |
| `workflow_job_status` | string | Workflow job status (successful, failed, etc.) |
| `artifacts` | object | Artifacts recorded on the workflow job |
| `created` / `started` / `finished` | string | ISO 8601 timestamps (required on the completed branch) |
| `error` | string | Error detail (required on the failed branch) |

The completed branch requires `workflow_job_id`, `created`, `started`, and
`finished`; the failed branch requires `error` and `workflow_job_id`, with all
other fields nullable.

**Result size limit:** the serialized result is bounded to the SDK's 1 MiB
limit. `artifacts` is truncated first — the largest keys are dropped and listed
under `__syntara_truncated_artifacts__` — and the workflow identity fields are
never dropped to make room. A result that exceeds the limit with no artifacts
left fails terminally.

## Failure behavior

| Situation | Failure code | Retryable | Notes |
| --- | --- | --- | --- |
| Connection never established (before launch POST) | `UPSTREAM_TRANSIENT` | yes | Bounded internal retries on pre-launch reads |
| Launch response lost (POST may have been sent) | `OUTCOME_UNKNOWN` | no | Reconciled via the correlation token; never blindly re-POSTed |
| Launch HTTP 401 / 403 / 404 | `AUTHENTICATION_FAILED` / `AUTHORIZATION_FAILED` / `NOT_FOUND` | no | |
| Other launch rejection (incl. 500, and 429/502/503/504 with a received response) | `REMOTE_REJECTED` | no | A received status proves the launch did not happen |
| Missing/misconfigured integration endpoint, TLS settings, or credential | `POLICY_DENIED` | no | Endpoint validated before credential access |
| Invalid input (e.g. reserved `extra_vars` key, bad label names) | `INVALID_INPUT` | no | |
| Deadline reached, parent still non-terminal | `TIMEOUT` | no | Carries the known workflow job id |
| Poll contact lost (5 consecutive errors, or errors to the deadline) | `OUTCOME_UNKNOWN` | no | "Launched but lost contact"; carries the known job id |
| Parent reaches `failed`/`error`/`canceled` | — (result, not failure) | — | Returned as the failed branch of the result union; AO maps `status == "failed"` to node failure |

A parent that reaches a failure status is a **completed observation**: the step
does not fail the SDK invocation, it returns the failed result branch with the
preserved fields; AO must map `status == "failed"` to node failure
(AAP-98908).

## Cancellation behavior

On cancellation: if no workflow job id is known, the step reports `CANCELLED`
without contacting AAP. Once a job id is known, the step makes a best-effort
`POST .../workflow_jobs/{id}/cancel/` and then reports `CANCELLED`. The
outcome says a cancel was **requested** — it does not claim the workflow or
its child jobs have stopped, and a failed cancel request does not change the
outcome. Step workload cleanup does not establish the workflow's terminal
state; AAP owns the workflow and its children.

## Build

From the repository root:

```bash
make node-image NODE=aap-workflow TAG=local-test
```

The base image and `.tekton` pipelines are provisional scaffolding until
AAP-98910/AAP-98911 confirm the release route.
