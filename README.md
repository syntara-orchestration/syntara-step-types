# Syntara step types

Container-packaged workflow step types for the Syntara Execution Plane, built on the
`syntara-plugin-contracts` runtime. Each step is a typed `ActionStep[TInput, TOutput]`
served over the `ContainerService` gRPC ABI.

## Development

Requirements for the test suite:

- Python 3.12–3.14 and [uv](https://docs.astral.sh/uv/).
- The Podman CLI and a reachable Podman engine. The SDK build used by `test-build`
  requires Podman; a Docker or Rancher Desktop Docker endpoint alone is not enough.
- Network access on first setup to install locked Python dependencies and download
  any missing workload base images. The test stages keep their SDK archives locally
  and do not publish to a release registry.

Install the project and development dependencies once after checkout, or again when
the dependency lockfile changes:

```bash
make install
```

Set up the container engine for your operating system, then run the test targets:

```bash
make check
make test-unit
make test-build
make test-integration
make test-all
```

| Target | Container engine required? | What it does |
| --- | --- | --- |
| `make test-unit` | No | Runs step implementation and contract tests. |
| `make test-build` | Yes | Uses `syntara-plugin build` to create and verify SDK archives for all three workloads. |
| `make test-integration` | Yes | Loads those archives and tests the normal image entrypoints through gRPC. Requires a current successful `make test-build`. |
| `make test-all` | Yes | Runs unit, build, and integration stages in order, building each workload once. |

`make test-clean` removes only test artifacts and reports. The integration stage
checks that the SDK build artifacts are present, current, and match the connected
Podman architecture; it never rebuilds them on its own.

`make check` runs Ruff, mypy, and `test-unit`. `make test-ci` and
`make test-integration-ci` add JUnit reports under `test-results/`; the unit report
also includes coverage. On Linux, install Podman with the distribution's package
manager and verify the configured engine is reachable with `podman info`. On macOS,
install Podman Desktop, initialize its default machine once if needed, and start it
before running the build or integration targets:

```bash
podman machine init # once, only if no default machine exists
podman machine start podman-machine-default
podman info
```

Use the same Podman connection for the SDK build and integration stages. By default,
the targets inherit Podman's configured connection. If you have multiple
connections, set `CONTAINER_CONNECTION` to the intended name shown by
`podman system connection list`; for example:

```bash
CONTAINER_CONNECTION=podman-machine-default-root make test-all
```

That connection name is specific to a standard Podman machine and may differ on your
laptop. CI runs unit tests on Linux and macOS with Python 3.12–3.14, then builds and
tests images on native Linux AMD64 and ARM64 runners.

`TEST_PLATFORM=linux/amd64` or `TEST_PLATFORM=linux/arm64` can select an explicit
build platform. Integration requires that platform to match the connected Podman
runtime.

## Available steps

| Step | Folder | Local development image | AO Konflux image name |
| --- | --- | --- | --- |
| HTTP request | `http-request/` | `syntara-node-http-request` | — |
| Python script | `script-python/` | `syntara-node-script-python` | `ao-python-rhel9` |
| Bash script | `script-bash/` | `syntara-node-script-bash` | `ao-bash-rhel9` |

The `make node-image` targets call `syntara-plugin build` to build the plugin,
runtime binding, and workload image archives. They then load the workload archive
into the local container engine and apply the local development image name. Konflux
builds the Python and Bash step images separately for the AO devel and early-access
applications using the image names in the last column.

Each plugin folder contains its own `Containerfile`, SDK manifest, and example
input. Shared script execution code lives in `script-runtime/`. Build from the
repository root:

```bash
make node-images CONTAINER_ENGINE=podman TAG=local-test
make node-image NODE=script-python CONTAINER_ENGINE=podman TAG=local-test
```

The SDK uses Podman to create workload archives. `CONTAINER_ENGINE` selects where
Make loads and runs a release image after the SDK build. To push images, set
`REGISTRY` and `TAG`:

```bash
make node-images CONTAINER_ENGINE=podman REGISTRY=quay.io/your-org TAG=0.1.0
make push-node-images CONTAINER_ENGINE=podman REGISTRY=quay.io/your-org TAG=0.1.0
```

## Architecture

Steps implement `ActionStep[TInput, TOutput]` from `syntara-plugin-contracts`. The
runtime package provides gRPC transport (`ContainerService`), input validation, and
failure enveloping. Each container exposes port 50051 and serves multiple concurrent
invocations.

See [`docs/runtime.md`](docs/runtime.md) for the gRPC ABI, cancellation, and TLS.

## Runtime and SDK dependencies

Step packages and workload images depend on `syntara-plugin-contracts` for runtime
APIs. The workload recipes use the contracts version pinned in their `Containerfile`
or lockfile. The development dependency group also includes `syntara-plugin-sdk`
for tests that compile and inspect plugin descriptors.

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
