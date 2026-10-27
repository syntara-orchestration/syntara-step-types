# Script Step

Runs Python and Bash scripts as isolated container workloads on the Syntara
Execution Plane. Recreates the previous-generation `syntara-node-script` step
on the current plugin SDK (`syntara-plugin-contracts`, ABI
`syntara.container/v1alpha1`).

## Steps

One plugin, two Action step types, one workload image:

| Step | Identity | Interpreter | Output |
| --- | --- | --- | --- |
| Run Python script | `syntara.script-step.python` | Python 3.12 | `return_code`, `stdout`, `stderr`, `stdout_json` |
| Run Bash script | `syntara.script-step.bash` | Bash | `return_code`, `stdout`, `stderr` |

Both steps share one subprocess executor with an environment allowlist, a
1 MB per-stream output cap, process-group cleanup, and cooperative
cancellation. Invocation timeouts are enforced by the container runtime
server.

## Inputs

- `code` (string, required) — script source
- `environment` (object of strings, optional) — extra environment variables

## Building

Everything flows through the SDK CLI. Stage the wheels into `wheels/` (the
build context is author-owned), then:

```sh
syntara-plugin validate plugin.yaml
syntara-plugin test-init plugin.yaml       # generate per-target pytest scaffolding
syntara-plugin build plugin.yaml --with-workload   # builds, pushes, pins the digest
```

`--with-workload` requires the registry configured in `syntara-plugin.yaml`
(default here: a local `http://localhost:5000` development registry).

Run the image standalone for a quick Health probe:

```sh
podman run --rm -p 50051:50051 localhost:5000/syntara-script-step:0.1.0
```

## Executing on the Execution Plane

Submit `work.execute` with the image and the step identity as
`step_revision_id`. The workload must advertise the identity in `Health`
before `Execute` is accepted.
