# Syntara step types

Container-packaged workflow step types for the Syntara Execution Plane, built on the
`syntara-plugin-runtime` SDK. Each step is a typed `TaskStep[TInput, TOutput]` served
over the `ContainerService` gRPC ABI.

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
| Script (Python and Bash) | `script/` | `syntara-node-script` |

Each folder contains a `Containerfile`, SDK manifest, example inputs, and tests.
Build from the repository root so the workspace packages are resolved correctly:

```bash
make node-images CONTAINER_ENGINE=docker TAG=local-test
make node-image NODE=script CONTAINER_ENGINE=docker TAG=local-test
```

Podman is the default engine. Configure `REGISTRY` and `TAG` when publishing:

```bash
make push-node-images REGISTRY=quay.io/your-org TAG=0.1.0
```

## Architecture

Steps implement `TaskStep[TInput, TOutput]` from `syntara-plugin-runtime`. The SDK
owns gRPC transport (`ContainerService`), input validation, and failure enveloping.
Each container exposes port 50051 and serves multiple concurrent invocations.

See [`docs/runtime.md`](docs/runtime.md) for the gRPC ABI, cancellation, and TLS.

## SDK dependency

`syntara-plugin-runtime` is resolved from a local path by default (see `pyproject.toml`
`[tool.uv.sources]`). Update the path or switch to a published package version once
the SDK is released to a registry.

## Provenance

Extracted from `syntara-orchestration/syntara` (`backend/nodes`) and refactored to use
the `syntara-plugin-runtime` SDK. Original source is Apache 2.0 licensed.
