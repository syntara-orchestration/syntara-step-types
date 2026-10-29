# Syntara step types

Container-packaged workflow step types for the Syntara Execution Plane, built on the
`syntara-plugin-sdk` SDK. Each step is a typed `ActionStep[TInput, TOutput]` served
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

Steps implement `ActionStep[TInput, TOutput]` from `syntara-plugin-sdk`. The SDK
owns gRPC transport (`ContainerService`), input validation, and failure enveloping.
Each container exposes port 50051 and serves multiple concurrent invocations.

See [`docs/runtime.md`](docs/runtime.md) for the gRPC ABI, cancellation, and TLS.

## SDK dependency

Development installs resolve the published `syntara-plugin-sdk` package using `uv.lock`.
Container builds install version `0.1.0a1` from the package registry by default;
`--build-arg SDK_VERSION=...` selects another compatible release. Both the Makefile
and SDK workload builds work from this repository without a sibling SDK checkout
or staged wheels.

## Provenance

Extracted from `syntara-orchestration/syntara` (`backend/nodes`) and refactored to use
the `syntara-plugin-sdk` SDK. Original source is Apache 2.0 licensed.

## Konflux CI

The `.tekton/syntara-step-types-tests-devel-pull-request.yaml` PipelineRun references
`syntara-step-types-tests` from `ansible-automation-platform/aap-konflux-pipelines`.
It runs quality checks, unit tests on Python 3.12–3.14, and container gRPC integration
tests for pull requests targeting `devel`, and reports results to the PR.

Publish the pipeline's `0.1` bundle before enabling this trigger, then pin its
published digest in the bundle reference. See the pipeline README for tenant
secrets, service-account requirements, and rollout order.
