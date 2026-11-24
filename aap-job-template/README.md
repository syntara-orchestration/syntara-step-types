# AAP job template step

Launches an Ansible Automation Platform Controller job template, polls until
the job reaches a terminal state, and returns its job details on success. The
step uses the selected AAP integration for the Controller URL and the
`syntara.aap.auth` credential recipe for token or username/password
authentication.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `job_template_id` | integer | One of ID or name | — | Controller job template ID. Takes precedence over the name. |
| `job_template_name` | string | One of ID or name | — | Job template name, looked up within `organization_name`. |
| `organization_id` / `organizationId` | integer | No | — | Organization ID used for label resolution. |
| `organization_name` | string | For name lookups | — | Organization used to resolve template, inventory, and instance group names. |
| `inventory_id` / `inventory_name` | integer / string | No | — | Inventory override. |
| `extra_vars` | object | No | `{}` | Variables passed to the launch endpoint. |
| `limit`, `tags`, `skip_tags` | string | No | — | Prompt-on-launch options. `tags` maps to AAP's `job_tags`. |
| `labels` | string array | No | — | Labels appended to the template; missing labels are created in the organization (up to 100). |
| `job_credentials` | integer array | No | — | AAP credential IDs passed to the launched job. |
| `credentialNames` | string array | No | — | Reserved compatibility input; current Controller launch uses `job_credentials`. |
| `verbosity` | integer, 0–5 | No | `0` | Ansible verbosity. |
| `job_type` | `run` / `check` | No | — | Job type override. |
| `forks` | integer | No | — | Parallel forks override. |
| `job_slicing` | integer | No | — | Maps to AAP's `job_slice_count`. |
| `diff_mode` | boolean | No | — | Enable diff mode. |
| `execution_environment` | string | No | — | Reserved for a future prompt-on-launch override. |
| `instance_group_id` / `instance_group_name` | integer / string | No | — | Instance group override. |
| `timeout_seconds` | integer, 1–86400 | No | `3600` | Maximum time spent polling the running job. |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `job_id` | integer | Controller job ID. |
| `job_url` | string | Controller output page for the playbook job. |
| `job_status` | string | Final Controller job status (`successful` on completed results). |
| `artifacts` | object | Artifacts returned by Controller, or `{}`. |
| `created`, `started`, `finished` | string | Controller timestamps, or empty strings when absent. |

Controller states other than `successful` fail the step. The step cancels the
Controller job when its SDK invocation is cancelled. HTTP and authentication
errors are reported without logging credential values or Controller response
bodies.

## Trusted runtime context

The host must supply the selected integration as `aapEndpoint` (an HTTPS base
URL). It may also supply `aapVerifySSL` and `aapCACertificate` from the
integration's TLS configuration. The step does not accept a Controller URL or
authentication material in workflow inputs.

## Build

From the repository root:

```bash
make node-image NODE=aap-job-template TAG=local-test
```
