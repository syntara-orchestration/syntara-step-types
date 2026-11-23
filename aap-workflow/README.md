# aap-workflow step

Launches and observes a workflow job template on an existing Ansible Automation
Platform (AAP) instance from a container step. AAP owns the underlying workflow
and its child jobs; the step launches the template, observes the parent workflow
job to a terminal status, and projects the preserved result fields.

**Status: scaffold (plan Step 1).** Input/output contracts, integration type,
credential recipe, and the gRPC entrypoint are in place. The launch/observe
executor lands with plan Step 4 — see
[docs/aap-workflow-plan.md](../docs/aap-workflow-plan.md) and
[docs/aap-workflow-sdk-verification.md](../docs/aap-workflow-sdk-verification.md).

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
| `credential_name` | string | no | Declared logical AAP credential name |
| `timeout` | integer | no | Execution budget in seconds |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `workflow_job_id` | integer | AAP workflow job ID |
| `workflow_job_url` | string | URL to view the workflow job in the AAP UI |
| `workflow_job_status` | string | Terminal workflow job status |
| `artifacts` | object | Artifacts recorded on the workflow job (truncated to the result size limit) |
| `created` / `started` / `finished` | string | ISO 8601 timestamps |
| `error` | string | Error detail on the failure branch |

## Build

From the repository root:

```bash
make node-image NODE=aap-workflow TAG=local-test
```

The base image and `.tekton` pipelines are provisional scaffolding until
AAP-98910/AAP-98911 confirm the release route.
