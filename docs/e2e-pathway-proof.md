# E2E pathway proof: product step-types → EP → AO

> Acceptance path for the reusable migration pathway.  
> **Product images + identities** are required. Fixture-era `script.v1` /
> `syntara-sdk-script` / `migration-test` are **not** sufficient acceptance.

Companion docs: [`compatibility-matrix.md`](compatibility-matrix.md),
[`migration-pathway.md`](migration-pathway.md).

## What was proven locally

On this branch:

```bash
make node-image NODE=script-python TAG=pathway-proof
# image: localhost/syntara-node-script-python:pathway-proof
# contracts pin inside image: syntara-plugin-contracts==0.1.0a4
```

Direct gRPC probe (no EP cluster). This run registered the static step identity through `step_registry_from_bindings`. It does not prove the compiled-revision Health and Execute gate in the compatibility matrix.

1. Health advertised the static identity `syntara.script-python.script-python`
2. Execute with inputs `{code, environment}` (**no** `language`) returned
   business output:
   - `return_code: 0`
   - `stdout` / `stderr`
   - `stdout_json: {"result": 42}`
3. Wrapper keys (`statusCode`, `errorResult`) were **not** in the business
   output object (they stay on the SDK `ContainerResult` envelope)

Repeat anytime — the gRPC integration tests assert Health identity, the
frozen business fields, and the absence of wrapper keys for both python and
bash:

```bash
make node-images TAG=pathway-proof
TEST_TAG=pathway-proof make test-integration
```

See `tests/integration/test_script_grpc.py`.

## EP kind dispatch (optional, cluster required)

```bash
# after EP kind target is up (see syntara-execution-plane docs)
# kind reads Docker's store, so transfer an archive from the default Podman build
podman save localhost/syntara-node-script-python:pathway-proof --output /tmp/script-python.tar
kind load image-archive /tmp/script-python.tar --name <ep-kind-name>

# product payload helper (EP tools)
cd ../syntara-execution-plane
uv run python -m tools.submit_work_item \
  --product-step-types \
  --language python \
  --image localhost/syntara-node-script-python:pathway-proof \
  --code 'import json; print(json.dumps({"result": 42}))' \
  --wait

# or automated kind test
EP_STEP_TYPES_IMAGE=localhost/syntara-node-script-python:pathway-proof \
  uv run pytest tests/integration/kind/test_step_types_product_dispatch.py -m integration
```

Assert work item `completed` and result business fields include `return_code` /
`stdout` / `stderr` (no wrapper keys in the AO-facing output object).

## AO config

```yaml
# settings / env equivalent
script_nodes_enabled: true
node_container_images:
  script-python: localhost/syntara-node-script-python:pathway-proof   # or Quay digest
  script-bash: localhost/syntara-node-script-bash:<tag>
```

Run a workflow with a script node (`language=python`) and a downstream
expression such as `${script_node.stdout}` / `${script_node.stdout_json.result}`.

AO unit coverage for this mapping:
`syntara/backend/tests/unit/workflows/activities/test_ep_script_result_namespace.py`.

## Definition of done

- [x] Product `script-python` image builds with contracts `0.1.0a4`
- [x] Health advertised the static identity `syntara.script-python.script-python` (identity-keyed registry; not the compiled-revision gate)
- [x] Execute with stripped inputs returns frozen business fields
- [x] EP tooling can submit product payloads (`--product-step-types`)
- [x] EP unit test locks payload shape; optional kind test gated on `EP_STEP_TYPES_IMAGE`
- [x] AO unit test proves `${script_node.*}` against business fields
- [ ] Full AO Temporal + EP callback workflow in a shared environment (operator soak; same contracts)
