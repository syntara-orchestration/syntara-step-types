# Standalone node repository: CI and Konflux plan

Prepared 30 September 2026. Proposed repository name: `syntara-step-types` (placeholder until confirmed).

## Recommendation

Move the existing `backend/nodes` uv workspace to the new repository root. Keep five independently buildable images, one shared runtime, and one shared gRPC protocol package. Use GitHub Actions for contributor checks and container smoke tests; use Konflux for the canonical release image builds, supply-chain checks, integration tests, and promotion.

Start with one development branch, one Konflux Application, five Components, and a coordinated version for the five images. Build all five on relevant code changes initially. This keeps shared runtime/protocol changes and release verification straightforward.

This document is a proposed implementation plan. No workflows, cluster resources, images, or repository settings were changed during this review.

## What was inspected

Source: local `syntara-sdk-nodes` worktree, branch `feat/sdk-node-containers`, commit `8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265`. Findings describe checked-in configuration, not a verification that every pipeline currently succeeds in the hosted services.

| Existing configuration | What it provides | Treatment in the new repo |
| --- | --- | --- |
| [Node workflow](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.github/workflows/build-node-images.yml) | PR unit/type checks; manual five-image matrix producing OCI artifacts | Extend to required PR image builds and smoke tests |
| [Backend CI](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.github/workflows/ci-backend.yml) | Python checks, test matrix, coverage, backend contracts and service integration | Reuse conventions; bring over checks relevant to nodes |
| [Pre-commit CI](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.github/workflows/ci-pre-commit.yml) | Formatting/lint hooks and pinned Actions | Give the node workspace its own tooling/configuration |
| [Backend Konflux PR build](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.tekton/ansible-automation-orchestrator-backend-devel-pull-request.yaml) | `nexus-tenant`, bundled AAP container pipeline, hermetic prefetch, AMD64/ARM64 builds, temporary PR images | Adapt component/image/context/branch settings; validate bundle capabilities with tenant owners |
| [API Tekton tests](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.tekton/automation-orchestrator-api-tests-devel-pull-request.yaml) | Separate bundled `ao-api-tests` pipeline and integration runner | Write a focused node integration pipeline |
| [Hermetic backend build](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/backend/Containerfile) and [local build tooling](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/backend/tools/ci/local-hermetic-build.sh) | Dependency prefetch and offline installation patterns | Adapt for the smaller node dependency graph |
| [Requirements synchronization](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.github/workflows/konflux-requirements-sync.yml) | Bot-specific requirements regeneration | Initially use a read-only drift check; add automation only after bot identity and permissions are known |
| [Renovate](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/renovate.json), [Dependabot](https://github.com/syntara-orchestration/syntara/blob/8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265/.github/dependabot.yml), Snyk and Sonar workflows | Dependency updates, code/dependency scans, coverage analysis | Reconfigure paths/projects; establish ownership to avoid duplicate update PRs |

Existing Konflux files cover backend/UI builds for `devel` and `early-access`, plus API/UI test runs. No node-specific Konflux Components are defined in this checkout. Application, Component and release resource definitions were not found alongside these pipelines; their management location needs confirmation.

A concrete configuration mismatch: the inspected backend GitHub/Tekton build files reference `backend/containers/syntara/Containerfile`, while this checkout contains `backend/Containerfile`. Treat these files as reference patterns and validate every new path. This review did not establish whether that mismatch also exists on the currently deployed development branches.

## Repository boundary and layout

```text
syntara-step-types/
├── http-request/                 # Containerfile, manifest, src, tests, examples
├── agent/
├── script/                       # Python and Bash in one image
├── aap-job/
├── aap-workflow/
├── _shared/                      # syntara-node-runtime; includes shared AAP code
├── _protocol/                    # proto source, generated client/server code
├── schemas/
├── tests/
├── tools/
│   ├── smoke_images.py
│   └── ci/                       # exports, drift checks, offline builds, snapshot checks
├── .github/workflows/
│   ├── ci.yml
│   ├── security.yml
│   └── live-aap.yml              # protected, opt-in integration tests
├── .tekton/
│   ├── <node>-pull-request.yaml  # five files
│   └── <node>-push.yaml          # five files
├── pipelines/tests/node-smoke.yaml
├── konflux/                      # desired resource templates/onboarding documentation
├── docs/                         # build, release, integration and compatibility guides
├── pyproject.toml
├── uv.lock
├── requirements.txt              # generated external runtime dependencies
├── requirements-build.txt        # generated/pinned build dependencies where needed
├── Makefile
├── .containerignore
├── .dockerignore
└── renovate.json
```

Every Containerfile continues to use the repository root as its build context, allowing access to `_shared`, `_protocol`, schemas and the lockfile. Update image source labels and documentation links. Copy/adapt the parent Ruff configuration into the new workspace and add its own lint dependencies. Generalize environment-specific OpenShift examples. Export only tracked source: exclude local credentials, `.env`, `.secrets`, virtual environments and generated test output.

Syntara retains workflow routing, Temporal activities, Kubernetes dispatch/port-forwarding, feature configuration, and tests that compare node contracts with Syntara models. Those are consumer integration responsibilities.

The split needs a protocol distribution step: Syntara currently depends on `nodes/_protocol` by local path. Prefer publishing a versioned `syntara-node-protocol` wheel to an agreed package registry. A dependency pinned to an exact new-repository commit and `_protocol` subdirectory can bridge the migration if package publishing is unavailable. Validate either choice with Syntara's hermetic dependency build before removing the local package. Record protocol compatibility, SDK revision, and image versions together.

## GitHub Actions

Use a stable required `nodes-ci` result that checks every required job's outcome, including failure/cancellation. Run on PRs and the chosen development branch; add `merge_group` if the repository uses a merge queue. Avoid workflow-level path filtering that leaves a required check permanently pending.

| Gate | Planned checks |
| --- | --- |
| Workspace quality | Frozen install, Ruff format/lint, strict mypy, unit tests and coverage |
| Python support | Unit tests on 3.12–3.14, matching the declared supported range; image tests on the actual 3.12 runtime |
| Contracts | Regenerate protobuf/stubs with pinned generators and require a clean diff; validate manifests/examples against schemas; test gRPC envelope/output compatibility |
| Dependency inputs | Re-export runtime/build dependency inputs and fail on drift; lockfile remains the source of dependency versions |
| Images | Build five AMD64 images on PRs and run real gRPC tests against those images |
| Diagnostics | Upload JUnit, coverage, sanitized logs and image metadata on failure; bound retention |

The current smoke runner expects all five images, and uses the script image as its fixture/client image. Initially build and smoke all five in one job so the images share an engine. If builds are later split into matrix jobs, explicitly transfer/load their artifacts before smoke testing; images do not carry across GitHub runners automatically.

Preserve existing arbitrary-UID, read-only-root, writable `/tmp`, dropped-capability checks. Extend built-image cases to include Bash, error results, cancellation/deadlines and AAP failures. Keep deterministic fake HTTP/AAP/agent services for normal PR tests. Agent checks cover submission/acknowledgement; complete asynchronous callback behavior remains a Syntara integration test.

Contributor jobs use read permissions and no publishing or live-service credentials. Pin Actions and tool versions. Configure code/dependency scanning with the organization's chosen Snyk/Sonar setup when those projects and secrets exist; report unavailable scans explicitly. Avoid copying the privileged requirements auto-commit workflow as the default.

## Hermetic image work

The current node Containerfiles install Git through `dnf`, install uv through pip, and resolve/install the Git-pinned SDK during `uv sync`. They need changes before enabling hermetic Konflux builds. Hermetic builds disable build-time network access, so dependencies must already be available. [Konflux hermetic build documentation](https://konflux-ci.dev/docs/building/hermetic-builds/)

1. Select a digest-pinned builder containing the approved Python/uv/build tools, or prefetch the required RPMs and Python tooling. Keep the runtime image minimal and digest-pinned.
2. Export external runtime dependencies from the workspace lock, excluding local workspace projects from the prefetch input. Build local packages from the checked-out source. A shared dependency export is a practical first implementation; each final image should still install only its own package's dependency closure.
3. Supply all build dependencies needed for local packages and dependency source distributions. Decide whether policy permits prefetched wheels; verify both architectures, including native dependencies such as grpcio. Hermeto's pip support uses pinned requirements files and distinguishes runtime from build dependencies. [Konflux dependency prefetch documentation](https://konflux-ci.dev/docs/building/prefetching-dependencies/)
4. Prove how the exact SDK Git revision and `sdk-python` subdirectory are materialized offline. Check the installed Hermeto version's support before choosing a source archive, prefetched Git source, or a published SDK package. Do not silently substitute a different SDK revision.
5. Install without network access and without resolving newer package versions. If an offline lock transformation is needed for prefetched sources, verify its installed versions against the original lock.
6. Run a local prefetch plus network-disabled build for all five images, then repeat through Konflux on AMD64 and ARM64. Treat missing build dependencies as build failures.

Adapt the existing backend helper approach, but omit its backend-only dependencies, branding, frontend and unrelated compilation steps.

## Konflux and Tekton

Propose Application `syntara-step-types` and Components `syntara-node-http-request`, `syntara-node-agent`, `syntara-node-script`, `syntara-node-aap-job`, `syntara-node-aap-workflow`. Names are provisional and must be unique in the chosen tenant.

Each component points to the same repository/branch, root build context, and its own Containerfile. Generate PR/push PipelineRuns during onboarding, using the tenant's supported, digest-pinned build bundle. Configure temporary PR tags, immutable commit references, dependency prefetch, and both production architectures to match existing Syntara conventions.

Initially rebuild all five for code, shared library, protocol, schema, dependency, Containerfile or CI changes. This also lets the release gate require one source commit across all five images. If path-based optimization is introduced later, shared changes must still trigger all affected components, and release coherence checks must account for deliberately reused images.

Use a custom IntegrationTestScenario referencing `pipelines/tests/node-smoke.yaml`. It receives image digests from the Konflux Snapshot and runs the built images. Konflux supports referencing a Git repository and Tekton pipeline for these tests. [Integration test configuration](https://konflux-ci.dev/docs/testing/integration/adding/)

The Docker/Podman-specific smoke runner needs a Kubernetes lifecycle implementation. Prefer an isolated test pod per case with the node and a gRPC client sharing the pod network: this works with the existing loopback listener. Run fixtures locally in that pod or in disposable test services. Use a fresh node instance for each invocation. Include startup deadlines and cleanup in a `finally` path; do not require a Docker socket in Tekton.

Separately, retain a Syntara consumer test using its real dispatcher and authenticated Kubernetes port-forwarding. `ep-dev-workers` can remain the development execution target; it is not evidence of access to the `nexus-tenant` Konflux build environment. Keep their credentials and roles separate.

## Release and dependency maintenance

Use one release version initially, with five versioned image references and a machine-readable digest inventory recording source commit, protocol version and SDK revision. Consumers deploy by digest. Promote tested Konflux outputs; GitHub's test builds do not become a competing release image source.

Configure required integration and supply-chain checks with the tenant owners, including the actual scan, SBOM, signing/provenance and Conforma policy requirements. The inspected bundled pipeline references alone do not establish which checks the new application will receive. Prepare ReleasePlan inputs; coordinate ReleasePlanAdmission and destination registry configuration with release administrators.

**Block partial releases.** Konflux can construct intermediate snapshots containing one updated image and older images for the remaining components. Use group snapshot testing for multi-component PRs where available. For pushes, require a complete snapshot whose five source revisions match the release commit, then run the release smoke suite. Begin with explicit promotion of that verified snapshot. [Konflux monorepo build and snapshot guidance](https://konflux-ci.dev/docs/patterns/managing-monorepo-applications/)

Configure Renovate/MintMaker for workspace dependencies, SDK pins, base-image digests and Tekton bundle pins. Regenerate prefetch inputs in the same update. Keep Dependabot for GitHub Actions only if Renovate is not already responsible for them. Do not copy `devel`/`early-access` filters unchanged when using a different branch strategy.

Real AAP tests should be manual or scheduled in a protected environment, with CI-managed secret references and designated test templates. Cover job success/failure and a workflow with at least one child job. The earlier live workflow check used an empty workflow template, so it did not establish child-job execution. Do not make live AAP availability a normal contributor PR requirement.

## Implementation sequence and completion criteria

| Phase | Work | Completion criterion |
| --- | --- | --- |
| 1 — Prepare locally now | Create a standalone staging copy; port tool configuration; draft GitHub workflows, dependency export tools, Konflux templates and build/release docs | Clean checkout can install, check, build and smoke all five without the parent monorepo |
| 2 — Prove image builds | Implement offline dependency materialization and build checks | Five network-disabled builds succeed; architecture support is verified |
| 3 — Activate new repository | Initial commit, push/PR access, default branch, GitHub checks, branch rules and dependency bots | A real PR produces all required checks, including container smoke tests |
| 4 — Onboard Konflux | Five Components, GitHub integration, registry access, build accounts and custom integration scenario | PR and push builds succeed; snapshot tests exercise actual digests |
| 5 — Establish releases | Coherent snapshot gate, release resources, digest inventory, protocol package distribution | One tested five-image release is published and reproducibly identifiable |
| 6 — Switch Syntara | Pin external protocol package and image digests; run model parity and real workflow/dispatcher tests | Syntara uses released node images; rollback to prior digests works; duplicated node source can be removed |

Phases 1–2 can start before the new GitHub repository is ready. Phase 1 should live in a separate local staging directory or preparation branch, so the existing draft node PR remains reviewable. Local validation does not replace the real onboarding checks in phases 3–5.

## Values needed before activation

- Final repository name and initial branch. Proposed default: `main`, one active development stream.
- Confirmed Konflux tenant, onboarding owner and supported build bundle. Existing Syntara configuration names `nexus-tenant`; reuse requires confirmation.
- Destination image registry/repositories, release owner and policy requirements.
- Protocol package distribution location. Proposed default: a versioned wheel; exact Git pin as a transitional option.
- Production architecture agreement. Proposed default: AMD64 and ARM64 to match Syntara's existing build configuration.
- CI-managed AAP secret ownership and designated safe job/workflow templates when enabling live integration tests.

These values do not prevent preparing the repository structure and CI code locally.
