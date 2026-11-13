# AO ↔ SDK ↔ EP compatibility matrix

> First deliverable of the GA step-types migration pathway.  
> Script rows are **filled** against AO `devel` + step-types `devel`.  
> HTTP / agentic / AAP rows are **stubs** (AO contract frozen; EP cutover not done).  
> Schema dialect: JSON Schema **2020-12** (SDK and AO). The execution result stays a `StandardOutputWrapper`. AO expression namespaces use an interim projection of that wrapper (see Envelope rule). Do not publish `statusCode`, `statusMessage`, or `errorResult` as template fields.

## How to read this matrix

| Column | Meaning |
|---|---|
| AO node type | Workflow catalog / `NodeType` string |
| Plugin identity | Static step identity `namespace.plugin.step`. Not the compiled revision |
| Images | Local Make tag vs product/Konflux ref AO should configure |
| Input mapping | What AO may send vs what the plugin accepts |
| Output freeze | Business fields safe for `${node_id.field}` after EP→AO completion mapping |
| EP status | Whether AO dispatches this type to EP today |
| Rollout | Enablement knob |

**Identity and revision.** Rows record the static step identity (`namespace.plugin.step`, for example `syntara.script-bash.script-bash`). That string is not a compiled revision. **Target state:** Health advertises the `sha256:` digest of the compiled step projection, and Execute accepts only that exact digest; this matrix does not freeze digest values, so take them from the compiled descriptor or the image registration manifest for the release under test, and do not submit the static identity in place of the digest. **Today:** images still register through `step_registry_from_bindings`, so Health lists the static identity and Execute accepts it (see [`runtime.md`](runtime.md)); the digest gate applies once the images serve through the SDK resolved-revision path.

**Envelope rule:** The SDK result is a `StandardOutputWrapper`: `result` (the business object), `statusCode`, `statusMessage`, and `errorResult`. Plugins declare only that business object under `spec.output`. Until AO consumes the wrapper natively, AO keeps a thin interim projection for templates: `resultSchema` adds `status` (`completed` \| `failed`) and failed-path `error`, and `${node_id.field}` reads business fields from `result`. Do not reshape those business fields, and do not publish wrapper keys as template fields.

---

## Script (filled)

AO keeps a **single** node type `script` with `language: python|bash`. Step-types ships **two** plugins. AO must route **before** `WorkExecute`.

### Decision (P0)

1. Map `language=python` → step identity `syntara.script-python.script-python` + python image.  
2. Map `language=bash` → step identity `syntara.script-bash.script-bash` + bash image.  
3. **Do not** forward `language` into `WorkloadInvocation.inputs` (plugins use `additionalProperties: false`).  
4. **Do not** forward `execution_profile` into inputs; use it only for EP selectors (`_execution_target_id`).  
5. Retire fixture-era acceptance defaults: `script.v1` and the personal-namespace `migration-test` script image (AO today). `script.v1` is neither a step identity nor a compiled revision.

### Row: Python

| Field | Value |
|---|---|
| AO node type | `script` (`language=python`) |
| AO activity (today) | `execute_script_activity` → `ep_dispatch_activity` |
| Plugin | `syntara` / `script-python` / Action `script-python` |
| **Step identity** | `syntara.script-python.script-python` |
| Local image | `localhost/syntara-node-script-python:<tag>` (`Makefile` `NODES`) |
| Workload image ID | `script-python-runner` |
| Konflux / Quay (devel) | `…/automation-orchestrator-step-types-script-python-rhel9:…` |
| AO image map key (target) | `script-python` (or resolve from `language` into `node_container_images`) |
| AO image map today | `"script"` points at a personal-namespace migration-test image (**mismatch**) |
| Rollout today | `script_nodes_enabled` (default `false`) |
| EP status | **Yes** (script-only today), but identity/image still fixture-era |

**Input mapping**

| AO parameter | Plugin input | Rule |
|---|---|---|
| `language` | — | Consume for routing only; **strip** before EP |
| `code` | `code` (required) | Pass through |
| `environment` | `environment` | Pass through (string values) |
| `execution_profile` | — | Placement only; **strip** from inputs |
| `max_output_bytes` | `max_output_bytes` (optional, default `276480`) | AO schema today omits this; may add later or rely on plugin default |
| `_…` / engine timeout keys | — | Keep out of plugin inputs; timeout goes on `WorkloadInvocation.timeout_seconds` |

**Output freeze (business → `${node.*}`)**

| Field | Type | Notes |
|---|---|---|
| `return_code` | integer | Required on completed path |
| `stdout` | string | Required |
| `stderr` | string | Required |
| `stdout_json` | any | Optional; python only when stdout parses as JSON |

The interim AO projection also requires `status: "completed"` on the completed path. The failed path may null business fields and set `error`.

<!-- Machine-checked output freeze (see tests/test_matrix_drift.py). Do not
edit without updating script-python/manifest.yaml. The types map records each
field's JSON Schema type; ~ means the field is unconstrained. -->
```yaml
output_freeze:
  plugin: script-python
  required: [return_code, stdout, stderr]
  optional: [stdout_json]
  types:
    return_code: integer
    stdout: string
    stderr: string
    stdout_json: ~
```

**Failure mapping (summary)**

| Layer | Behavior |
|---|---|
| Plugin | `StepFailure` / structured error → nonzero wrapper status |
| EP | `failed` / `cancelled` / `outcome_unknown` on work item |
| AO | `work.execute.completed` → Temporal complete/fail; namespace still exposes available script fields |

---

### Row: Bash

| Field | Value |
|---|---|
| AO node type | `script` (`language=bash`) |
| Plugin | `syntara` / `script-bash` / Action `script-bash` |
| **Step identity** | `syntara.script-bash.script-bash` |
| Local image | `localhost/syntara-node-script-bash:<tag>` |
| Workload image ID | `script-bash-runner` |
| Konflux / Quay (devel) | `…/automation-orchestrator-step-types-script-bash-rhel9:…` |
| AO image map key (target) | `script-bash` |
| Same input strip rules as Python | yes |
| **Output freeze** | `return_code`, `stdout`, `stderr` only (**no** `stdout_json` in plugin schema) |

<!-- Machine-checked output freeze (see tests/test_matrix_drift.py). Do not
edit without updating script-bash/manifest.yaml. -->
```yaml
output_freeze:
  plugin: script-bash
  required: [return_code, stdout, stderr]
  optional: []
  types:
    return_code: integer
    stdout: string
    stderr: string
```

---

## HTTP request (stub)

| Field | Value |
|---|---|
| AO node type | `http_request` |
| AO activity today | `execute_http_request_activity` (**in-process**; not EP) |
| Plugin (step-types) | `syntara` / `http-request` / Action `http-request` |
| **Step identity** | `syntara.http-request.http-request` |
| Local image | `localhost/syntara-node-http-request:<tag>` |
| Workload image ID | `http-request-runner` |
| Konflux | **Missing on devel** (no HTTP Tekton pipelines yet) |
| EP status | Image EP-ready; **AO cutover deferred** |
| Rollout (target) | Future per-type flag (e.g. `http_request_nodes_enabled`) + image map entry |

**Input mapping (gaps to resolve at cutover)**

| AO parameter | Plugin input | Gap |
|---|---|---|
| `method`, `url`, `headers`, `query_params`, `body` | same names | Mostly aligned; AO requires `method`, plugin defaults `method=GET` |
| `credential_id` | — | AO-only today; credentials must not be baked into image (CSI / resolve path) |
| — | `timeout_seconds`, `follow_redirects` | Plugin has them; AO schema does not expose them yet |

**Output freeze (AO + plugin agree)**

| Field | Type |
|---|---|
| `status_code` | integer |
| `body` | any (JSON or text) |
| `headers` | object\<string,string\> |
| `elapsed` | number |

Plus the interim AO envelope: `status`, and `error` on the failed path.

<!-- Machine-checked output freeze (see tests/test_matrix_drift.py). Do not
edit without updating http-request/manifest.yaml. -->
```yaml
output_freeze:
  plugin: http-request
  required: [status_code, body, headers, elapsed]
  optional: []
  types:
    status_code: integer
    body: ~
    headers: object
    elapsed: number
```

---

## Agentic (stub)

| Field | Value |
|---|---|
| AO node type | `agentic` |
| AO activity today | `execute_agentic_activity` (Agent Orchestrator client + async signals) |
| Plugin on step-types `devel` | **None** |
| Legacy remote | `feat/agent-step` (pre-Plugin/Action; not checklist baseline) |
| EP status | **Not EP** — different async model |
| Special case | Invocation ack ≠ final AO `output`; pathway must not assume script-like `WorkExecute` completion alone |

**Output freeze (AO `AgenticOutput` / resultSchema)**

| Field | Notes |
|---|---|
| `output` | required on completed |
| `tool_calls` | optional |
| `used_tools` | optional |
| `structured_output_metadata` | optional |
| `integration_ids` | optional |

**Input freeze (AO today):** `prompt` (required), `agent`, `model` / `llm_model_id`, `credential_id`, tool selection fields, `response_schema`, `file_ids`, `integration_connections`.

---

## AAP job template (stub)

| Field | Value |
|---|---|
| AO node type | `aap_job_template` |
| AO activity today | launch/poll Controller in-process |
| Plugin on step-types `devel` | **None** (legacy on `feat/aap-steps*`) |
| EP status | Deferred; may stay hybrid (container talks to AAP) |
| Target identity / image | TBD when ported to Plugin/Action |

**Output freeze (AO)**

| Field |
|---|
| `job_id`, `job_url`, `job_status`, `artifacts`, `created`, `started`, `finished` |

---

## AAP workflow job template (stub)

| Field | Value |
|---|---|
| AO node type | `aap_workflow_job_template` |
| AO activity today | launch/poll Controller in-process |
| Plugin on step-types `devel` | **None** |
| EP status | Deferred / hybrid |
| Target identity / image | TBD |

**Output freeze (AO)**

| Field |
|---|
| `workflow_job_id`, `workflow_job_url`, `workflow_job_status`, `artifacts`, `created`, `started`, `finished` |

---

## Cross-cutting rules

1. **Breaking change:** renaming/removing a frozen output field requires an approved alias period; saved `${node.field}` strings are not schema-versioned at save time.  
2. **Thin projection:** expression namespaces expose business fields and the interim AO `status` / `error` envelope. Do not expose `statusCode`, `statusMessage`, or `errorResult` from `StandardOutputWrapper` as template fields.  
3. **Health and Execute (target state):** the workload must advertise the exact compiled revision AO submits. The static step identity is not that revision, and `*` is not an Execute match. `script.v1` is not a compiled revision. Until the images serve the resolved-revision path, Health and Execute accept the static identity (see [`runtime.md`](runtime.md)).  
4. **Contracts pin:** EP worker and step images must share a reviewed `syntara-plugin-contracts` version. **Current joint pin: `0.1.0a4`.**  
5. **No silent fallback:** after an ambiguous EP submit, do not fall back to in-process execution (current AO isolation rule).  
6. **EP `output_config`:** key selection only until AO evaluates template expressions; AO remains source of truth for `${…}` expansion.

---

## Sources

| Layer | Source |
|---|---|
| AO schemas | `syntara/backend/src/syntara/schemas/workflows/v2/executors/{script,http_request,agentic,aap_*}.schema.json` (AO `devel`) |
| AO output models | `…/workflow_definition.py` → `ScriptOutput`, `HttpRequestOutput`, `AgenticOutput`, `AAP*Output` (AO `devel`) |
| AO dispatch | `…/workflows/workflow_engine/activities/ep/ep_dispatch_activity.py`, `node_container_images`, `script_nodes_enabled` (AO `devel`) |
| Step plugins | `script-python/`, `script-bash/`, `http-request/` manifests + `Makefile` / `.tekton` (this repo, `devel`) |
| EP protocol | `syntara-execution-plane` `docs/workload-protocol.md`, `docs/integration.md` (EP `devel`) |
