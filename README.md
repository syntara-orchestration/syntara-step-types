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

| Step | Folder | Local development image | AO Konflux image name |
| --- | --- | --- | --- |
| HTTP request | `http-request/` | `syntara-node-http-request` | — |
| Python script | `script-python/` | `syntara-node-script-python` | `ao-python-rhel9` |
| Bash script | `script-bash/` | `syntara-node-script-bash` | `ao-bash-rhel9` |

The local image names are used by the `make node-image` targets. Konflux builds the
Python and Bash step images separately for the AO devel and early-access applications
using the image names in the last column.

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
APIs. Container builds install version `0.1.0a4` by default (aligned with
Execution Plane); set `CONTRACTS_VERSION=...` when running `make node-images` or
`make node-image` to select another release. The development dependency group also
includes `syntara-plugin-sdk` for tests that compile and inspect plugin descriptors.

AO ↔ plugin identity/output freezes for EP dispatch live in
[`docs/compatibility-matrix.md`](docs/compatibility-matrix.md).
The reusable cutover checklist is
[`docs/migration-pathway.md`](docs/migration-pathway.md).
The product-image E2E proof is
[`docs/e2e-pathway-proof.md`](docs/e2e-pathway-proof.md)
(gRPC integration tests under `tests/integration/`).

## Provenance

Extracted from `syntara-orchestration/syntara` (`backend/nodes`) and refactored to use
the `syntara-plugin-contracts` runtime. Original source is Apache 2.0 licensed.
