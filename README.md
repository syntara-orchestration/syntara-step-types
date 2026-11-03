# Syntara step types

Container-packaged workflow step types for the Syntara Execution Plane, built on the
`syntara-plugin-contracts` runtime. Each step is a typed `ActionStep[TInput, TOutput]`
served over the `ContainerService` gRPC ABI.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
make install
make check
```

`make check` runs Ruff, mypy, and unit tests.
`make test-ci` writes JUnit and coverage reports to `test-results/`.
CI runs tests on Python 3.12–3.14.

## Available steps

| Step | Folder | Image suffix |
| --- | --- | --- |
| HTTP request | `http-request/` | `syntara-node-http-request` |
| Python script | `script-python/` | `syntara-node-script-python` |
| Bash script | `script-bash/` | `syntara-node-script-bash` |

Each plugin folder contains its own `Containerfile`, SDK manifest, and example
input. Shared script execution code lives in `script-runtime/`. Build from the
repository root:

```bash
make node-images CONTAINER_ENGINE=docker TAG=local-test
make node-image NODE=script-python CONTAINER_ENGINE=docker TAG=local-test
make node-image NODE=script-bash CONTAINER_ENGINE=docker TAG=local-test
```

Podman is the default engine. Configure `REGISTRY` and `TAG` when publishing:

```bash
make push-node-images REGISTRY=quay.io/your-org TAG=0.1.0
```

## Architecture

Steps implement `ActionStep[TInput, TOutput]` from `syntara-plugin-contracts`. The
runtime package provides gRPC transport (`ContainerService`), input validation, and
failure enveloping. Each container exposes port 50051 and serves multiple concurrent
invocations.

See [`docs/runtime.md`](docs/runtime.md) for the gRPC ABI, cancellation, and TLS.

## Runtime and SDK dependencies

Step packages and workload images depend on `syntara-plugin-contracts` for runtime
APIs. Container builds install version `0.1.0a2` by default; set
`CONTRACTS_VERSION=...` when running `make node-images` or `make node-image` to select
another release. The development dependency group also includes `syntara-plugin-sdk`
for tests that compile and inspect plugin descriptors.

## Provenance

Extracted from `syntara-orchestration/syntara` (`backend/nodes`) and refactored to use
the `syntara-plugin-contracts` runtime. Original source is Apache 2.0 licensed.
