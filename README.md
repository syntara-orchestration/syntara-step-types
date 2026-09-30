# Syntara workflow node containers

Local staging repository for five SDK-based gRPC workflow node images. The new
upstream repository has not been connected yet. See [source provenance](docs/SOURCE.md)
and [upstream handoff](docs/upstream.md).

| Workflow type | Package folder / image suffix |
| --- | --- |
| `http_request` | `http-request` |
| `agentic` | `agent` |
| `script` (Python and Bash) | `script` |
| `aap_job_template` | `aap-job` |
| `aap_workflow_job_template` | `aap-workflow` |

Each folder contains a Containerfile, manifest, source, tests and example input.
`_shared` supplies the runtime and AAP helpers; `_protocol` supplies the gRPC
contract. Syntara workflow dispatch and Temporal integration remain in Syntara.

## Install and check

Use Python 3.12 and uv 0.12.3 (the version pinned in CI and the Containerfiles):

```bash
make install
make check
```

If an older uv is installed, use this override for any Make target:

```bash
make install check UV='uvx --from uv==0.12.3 uv'
```

`make check` runs Ruff, mypy, tests, and generated-file checks. `make test-ci`
additionally writes JUnit and coverage reports to the ignored `test-results/`.
The workspace declares Python 3.12–3.14 support and CI tests each version.

After changing dependencies, run `uv lock` and `make sync-requirements`. The
requirements export contains external runtime dependencies; local workspace
packages are built from this repository. It is an input for future hermetic
build work, not a complete offline dependency bundle. After editing the protobuf
contract, run `make proto` and commit the generated code/stubs.

## Build and smoke-test images

Run from this repository's root; it is the build context for every image.
Docker or Podman must be running. All five images must exist for smoke tests.

```bash
make node-images CONTAINER_ENGINE=docker TAG=local-test
make smoke-images CONTAINER_ENGINE=docker TAG=local-test

# Build one image:
make node-image NODE=script CONTAINER_ENGINE=docker TAG=local-test
```

The smoke runner creates disposable containers/network, uses fake HTTP/AAP/agent
services and cleans up its resources. It checks all five images over real gRPC,
including Python, Bash and a nonzero script exit. Nodes run as an arbitrary UID
with a read-only root filesystem, writable temporary storage and dropped
capabilities. It does not contact a real AAP instance or OpenShift cluster.

Builds target the container engine's architecture. GitHub uses Linux AMD64;
production Konflux templates propose AMD64 and ARM64. Multiarchitecture release
builds still need validation during onboarding.

Publish only after choosing a real registry and upstream source URL:

```bash
make node-images REGISTRY=quay.io/YOUR_ORG TAG=YOUR_VERSION SOURCE_URL=https://github.com/YOUR_ORG/YOUR_REPO
make push-node-images REGISTRY=quay.io/YOUR_ORG TAG=YOUR_VERSION
```

`VCS_REF` defaults to the current Git commit and can be overridden for builds from
an exported checkout. Image source labels currently default to the original
Syntara repository; CI supplies its actual repository URL. Deploy by image digest.

## CI and Konflux status

[Nodes CI](.github/workflows/ci.yml) runs on pull requests, pushes to `main`, merge
queue events and manual dispatch. It runs quality, generated-file, test-matrix
and five-image smoke gates. Configure `nodes-ci` as the required branch check once
upstream is connected. It has read permissions and does not publish images.

[Konflux templates](konflux/README.md) prepare ten PR/push PipelineRuns, one pair
per image. They are deliberately outside `.tekton` and have unresolved onboarding
values. The Containerfiles still perform online installs. Hermetic builds,
snapshot-based Kubernetes tests, release signing/promotion and protected live AAP
tests remain onboarding work, documented in the [CI plan](docs/ci-plan.md).

## Runtime and consumer integration

Every image exposes `syntara.node.v1.NodeService` on port 50051 with `Health`,
streaming `Execute` and `Cancel`. The default listener is pod loopback; remote
direct connections require mutual TLS. Each node accepts one invocation.

See the [runtime guide](docs/runtime.md) for port-forwarding, authentication,
cancellation, result compatibility and Syntara configuration. Syntara needs a
versioned or commit-pinned external protocol package before its local
`nodes/_protocol` dependency can be removed.
