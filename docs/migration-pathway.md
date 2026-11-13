# Migration pathway: GA step types → Plugin SDK → Execution Plane

> Playbook for the GA step-types migration.  
> Golden reference: AO script dispatch on `devel` + the plugins in this repo (`http-request`, `script-python`, `script-bash`).  
> Normative I/O freezes: [`compatibility-matrix.md`](compatibility-matrix.md).  
> Runtime ABI details: [`runtime.md`](runtime.md).

This document is the **reusable checklist** for moving an existing Automation
Orchestrator (AO) executor onto a step-types container image that Execution
Plane (EP) can run. It does **not** itself port agent/AAP steps; those follow
the same steps when ready.

## Ownership cheat-sheet

| Layer | Owns | Does not own |
|---|---|---|
| **Step package (this repo)** | `plugin.yaml` / Action manifest, Pydantic models, `ActionStep.run()`, Containerfile, examples, unit/integration tests | Workflow catalog, Temporal, cluster scheduling |
| **SDK (`syntara-plugin-sdk` / `syntara-plugin-contracts`)** | Manifest schemas (JSON Schema **2020-12**), `StandardOutputWrapper`, gRPC `syntara.container/v1alpha1`, CLI validate/run/build, harness | Product image publishing, AO expressions |
| **Execution Plane** | `work.execute`, Health→Execute fence, result persist, cancel, `work.execute.completed` callback | Plugin discovery, step I/O schema validation, `${node.field}` evaluation |
| **Automation Orchestrator** | Node `parameterSchema` / `resultSchema`, expression namespaces, image map, enable flags, `WorkExecute` submit, callback→Temporal, credential resolve policy | In-image step implementation |

Authors return **business output only** from `run()`. The SDK wraps
`StandardOutputWrapper`. AO namespaces expose **business fields**, never
wrapper keys (`statusCode`, `statusMessage`, `errorResult`).

## End-to-end flow (script golden path)

```text
AO workflow node (script + language)
  → ep_dispatch_activity
      route language → step identity + image key
      submit the compiled revision for that identity (not the identity string)
      strip language / execution_profile from inputs
      WorkExecute { image, invocation, selectors?, output_config? }
  → EP worker
      Health (ABI + advertised revision)
      Execute once → ContainerResult
      persist → work.execute.completed
  → AO bridge
      Temporal complete/fail
      set_namespace(node_id, business fields)
  → ${node_id.stdout} / ${node_id.return_code} / …
```

## Checklist (follow in order)

### 1. Freeze the AO contract

1. Read the current AO executor schemas under
   `syntara/backend/src/syntara/schemas/workflows/v2/executors/`.
2. Record the row in [`compatibility-matrix.md`](compatibility-matrix.md):
   - AO node type string
   - Required/optional parameters
   - Completed-path output fields used by `${node_id.field}`
   - Failed-path / envelope fields AO owns (`status`, `error`)
3. **Rule:** renaming or removing a frozen output field is a breaking change
   unless an alias period is approved. Save-time template checks do not
   validate field paths.

### 2. Align or create the step-types plugin

Canonical layout (current product plugins; evolve toward SDK `steps/<name>/`
when convenient):

```text
<plugin>/
  plugin.yaml            # kind: Plugin; namespace + name → identity prefix
  syntara-plugin.yaml    # build/publish settings
  runtime-bindings.yaml  # kind: RuntimeBinding; workload image options
  manifest.yaml          # kind: Action; input/output/error schemas
  Containerfile
  example.json
  README.md
  [src/ …]               # ActionStep[TIn, TOut] (+ shared runtime package if needed)
```

Requirements:

1. `apiVersion: syntara.io/v1alpha1`
2. Stable identity `namespace.plugin.step` (e.g. `syntara.http-request.http-request`)
3. `spec.input` / `spec.output` JSON Schema **2020-12** mirroring Pydantic models
4. `ActionStep.run()` returns the output model only; raise `StepFailure` for
   deliberate failures
5. Declare each logical workload in `plugin.yaml` `spec.workloads.<id>` and its
   image options in `runtime-bindings.yaml`; build targets live under
   `build.workloads` in `syntara-plugin.yaml`
6. Health must advertise the **exact** compiled revision AO will submit (target
   state; today's `step_registry_from_bindings` images serve the static
   identity, see [`runtime.md`](runtime.md)). The static identity
   (`namespace.plugin.step`) is not that revision.
7. Pin `syntara-plugin-contracts` to the same reviewed version as EP
   (joint pin: **`0.1.0a4`**)

Copy an existing plugin (`script-python` or `http-request`) rather than
scaffolding from scratch unless the SDK `syntara-plugin init` path matches
your runner kind.

### 3. Map AO parameters → plugin inputs

Before `WorkExecute`:

| Pattern | Rule |
|---|---|
| Routing-only fields (e.g. script `language`) | Use to pick identity/image; **do not** put in `invocation.inputs` |
| Placement (`execution_profile`) | Convert to EP selectors (e.g. `_execution_target_id`); strip from inputs |
| Engine / `_`-prefixed keys | Keep out of plugin inputs; map timeout to `invocation.timeout_seconds` |
| Plugin-legal fields | Pass through only keys declared in the Action `spec.input` |

Script golden mapping is already documented in the compatibility matrix.

### 4. Wire AO dispatch (clone the script spine)

Reference implementation:
`syntara/backend/src/syntara/workflows/workflow_engine/activities/ep/ep_dispatch_activity.py`.

For each new EP-backed type, reuse this pattern:

1. Feature / enable gate **before** contacting EP (script today:
   `script_nodes_enabled`)
2. Validate AO parameter model
3. Resolve image from `settings.node_container_images[<plugin-key>]`
4. Build `WorkloadInvocation`:
   - `workflow_run_id` from Temporal activity info
   - revision field = compiled `sha256:` digest for that step identity, not the identity string (target state; today's images accept the static identity)
   - `inputs` = stripped plugin payload
   - `timeout_seconds` from engine/node timeout
5. Stable work id: `uuid5(execution_id, temporal_activity_id)` (same as
   `ActivityExecution` / command id)
6. Submit `WorkExecute` via `ExecutionPlaneHttpClient`
7. Non-terminal states → `activity.raise_complete_async()`; completion arrives
   on `work.execute.completed`
8. On result: project `StandardOutputWrapper.result` into the node namespace
   used by `${node_id.field}`. Keep the interim AO envelope (`status` /
   `error`) thin; do not reshape business fields or publish wrapper keys.

**Do not** silently fall back to in-process execution after an ambiguous EP
submit (current AO isolation rule).

External docs (sibling repos):

- AO: `syntara/backend/docs/execution-plane/integration.md`
- EP: `syntara-execution-plane/docs/workload-protocol.md`
- EP: `syntara-execution-plane/docs/integration.md`

Prefer those and the current `ep_dispatch_activity` code over older
binding/outbox narratives.

### 5. Prove locally (step package)

```bash
# from syntara-step-types
make sdk-plugin-validate PLUGIN=<plugin>
make sdk-plugin-run PLUGIN=<plugin>
make sdk-plugin-drift          # compatibility-matrix output freeze ↔ manifest checks
# unit tests for the package / shared runtime
```

Optional SDK harness (when using SDK testing helpers): `invoke_step` and
`invoke_step_in_container` equivalence for the new step.

### 6. Prove in container

```bash
make node-image NODE=<plugin> TAG=<tag>
# existing gRPC integration tests under tests/integration/
```

Confirm Health lists the revision Execute accepts (the compiled digest on the
resolved-revision path, the static identity on today's
`step_registry_from_bindings` images; see [`runtime.md`](runtime.md)), and
Execute accepts the stripped inputs from the matrix.

### 7. Prove on Execution Plane

1. Load/push the image to the EP target cluster
2. Submit `work.execute` with that image and the exact compiled revision Health advertised
3. Assert work reaches `completed` (and exercise failed/cancel paths)
4. Confirm persisted result carries business fields EP mapped from
   `ContainerResult` — not fixture-only `script.v1` / `sdk_script` acceptance

### 8. Prove through AO

1. Set `node_container_images` for the plugin key(s)
2. Enable the type’s gate
3. Run a workflow that uses frozen `${node_id.<field>}` expressions
4. Assert namespaces match the matrix business fields after completion

Fixture-era defaults (`script.v1`,
the personal-namespace `migration-test` script image, EP `tests/fixtures/sdk_script`)
are **not** sufficient acceptance for a product cutover.

### 9. Release

1. Versioned OCI reference (Konflux/Quay or agreed registry)
2. Record `syntara-plugin-contracts` pin alongside EP
3. Ensure the plugin is listed in `Makefile` `NODES` / `SDK_PLUGINS` and CI
   (`sdk-plugin-ci`)
4. Add/confirm Tekton/Konflux pipelines (HTTP still needs Konflux on devel)
5. Update [`compatibility-matrix.md`](compatibility-matrix.md) EP status +
   image refs

## Definition of done (per step type)

- [ ] Matrix row filled (inputs, identity, image keys, output freeze)
- [ ] Plugin validate / run / drift / unit+integration tests green
- [ ] Health advertises the exact compiled revision AO submits
- [ ] AO dispatch uses product image map keys and stripped inputs
- [ ] EP `work.execute` completes with expected business output
- [ ] AO workflow `${node_id.field}` works without reading wrapper keys
- [ ] Enable gate + image/revision pin documented
- [ ] OCI + contracts pin recorded

## Current status snapshot

| Step type | Plugin in this repo | AO EP dispatch | Playbook consumer |
|---|---|---|---|
| Script (python/bash) | Yes | Yes (golden path) | Reference implementation |
| HTTP request | Yes | Not yet (in-process AO) | Next cutover — clone script spine |
| Agentic | No | No (Agent Orchestrator async) | Special-case checklist later |
| AAP job / workflow | No | No (Controller poll) | Hybrid possible; port Plugin/Action first |

## Related links

| Doc | Location |
|---|---|
| Compatibility matrix | [`docs/compatibility-matrix.md`](compatibility-matrix.md) |
| E2E pathway proof | [`docs/e2e-pathway-proof.md`](e2e-pathway-proof.md) |
| Runtime / gRPC ABI | [`docs/runtime.md`](runtime.md) |
| SDK verification notes | [`SDK_VERIFICATION.md`](../SDK_VERIFICATION.md) |
| SDK authoring guide | `syntara-plugin-sdk/docs/authoring-guide.md` |
| AO EP integration | `syntara/backend/docs/execution-plane/integration.md` |
| EP workload protocol | `syntara-execution-plane/docs/workload-protocol.md` |
