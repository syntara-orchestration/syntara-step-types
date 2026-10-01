# Syntara step types

SDK runtime and gRPC interfaces for workflow steps executed by the Execution Plane.
Contributions target `devel`. Syntara owns workflow routing, Temporal activities
and execution infrastructure; this repository supplies portable step packages.

## Development

Use Python 3.12 and uv 0.12.3:

```bash
make install
make check
```

With an older local uv, pass `UV='uvx --from uv==0.12.3 uv'` to Make.
`make check` runs Ruff, mypy, unit tests and generated-file checks.
`make test-ci` writes JUnit and coverage reports to `test-results/`.
CI runs tests on Python 3.12–3.14. It accepts PRs against development and stacked
feature branches; pushes and merge queue events target `devel`. The required
aggregate check is `nodes-ci`.

The root `pyproject.toml` declares the packages present at this stage. `uv.lock`
pins their dependencies, including the SDK source revision. `_shared` contains
the runtime and shared types; `_protocol` contains the protobuf contract,
generated code and CLI. Regenerate bindings with `make proto` after protocol edits.
See [the runtime guide](docs/runtime.md) for invocation, TLS and cancellation.

## Available steps and container builds

| Step | Folder / image suffix |
| --- | --- |
| HTTP request | `http-request` |
| Script (Python and Bash) | `script` |
| AAP job | `aap-job` |
| AAP workflow | `aap-workflow` |

Each folder has a Containerfile, SDK manifest, example inputs and tests. Build
from the repository root so every image can access the shared workspace packages:

```bash
make node-images CONTAINER_ENGINE=docker TAG=local-test
make smoke-images CONTAINER_ENGINE=docker TAG=local-test
make node-image NODE=script CONTAINER_ENGINE=docker TAG=local-test
```

Podman is also supported and is the default engine. Smoke tests require all images
listed above, including the script image used as the fixture and protocol client.
They use disposable mock services and check real gRPC calls, arbitrary UIDs,
read-only filesystems, Python/Bash output and script failures. They clean up their
containers and network. CI builds and smoke-tests these same available images.

Images target the container engine's architecture. Configure `REGISTRY` and `TAG`
when publishing with `make push-node-images`; use immutable digests in deployments.
`SOURCE_URL` defaults to this repository and `VCS_REF` to the current commit.
Publishing is an explicit operation; GitHub CI builds and tests images locally.

## Konflux preparation

[Konflux onboarding](konflux/README.md) describes the inactive pipeline templates
and remaining offline build/release work. `make sync-requirements` exports pinned
external runtime dependencies; CI checks this export for drift. The export alone
does not make the current Containerfiles hermetic.

## Provenance

Extracted from `syntara-orchestration/syntara`, commit
`8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265`, under `backend/nodes`.
The SDK revision is pinned in `pyproject.toml`. The upstream Apache 2.0 LICENSE
and applicable source attribution are retained. NOTICE records relevant upstream
notices; `uv.lock` records this workspace's dependency versions.
