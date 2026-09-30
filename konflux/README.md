# Konflux onboarding templates

`templates/` contains ten Tekton PipelineRun templates: PR and push builds for each
of the five node images. They adapt Syntara's bundled container-pipeline pattern.
They are **not active pipelines** and must not be copied into `.tekton` unchanged.

Prepare one Application and five Components using the tenant's supported onboarding
workflow. Use the same repository and branch for each, context `.`, and these paths:

| Component | Containerfile |
| --- | --- |
| syntara-node-http-request | http-request/Containerfile |
| syntara-node-agent | agent/Containerfile |
| syntara-node-script | script/Containerfile |
| syntara-node-aap-job | aap-job/Containerfile |
| syntara-node-aap-workflow | aap-workflow/Containerfile |

Compare the newly generated onboarding PipelineRuns with these templates. Preserve
tenant-required tasks, scan policies and generated service-account/secret names.
Build all five on PR/push initially, so a shared change has one coherent release.

## Template values

| Placeholder | Value to obtain |
| --- | --- |
| `__APPLICATION__` | Application name in the selected tenant |
| `__TENANT__` | Konflux namespace; confirm whether existing `nexus-tenant` is appropriate |
| `__BRANCH__` | Actual repository development branch |
| `__IMAGE_PREFIX__` | Registry/organization path without trailing slash |
| `__BUILD_BUNDLE__` | Approved container pipeline bundle including immutable digest |
| `__BUNDLE_PULL_SECRET__` | Tenant's bundle resolver pull secret |
| `__BUILD_SERVICE_ACCOUNT__` | Component-specific generated build account |

`{{revision}}`, `{{repo_url}}`, `{{target_branch}}`, `{{pull_request_number}}` and
`{{ git_auth_secret }}` are Pipelines as Code substitutions; retain them.
Confirm AMD64/ARM64 runner names against the selected tenant's build bundle.

## Required before enabling hermetic builds

The templates request `hermetic: "true"`. Current Containerfiles still install
Git, uv and the Git-pinned SDK online, so they will not pass those builds yet.
The generated root `requirements.txt` is only the initial external runtime input.

Prepare an approved builder or RPM/tool prefetch, pin and supply Python build
dependencies, materialize the exact SDK revision/subdirectory offline, and install
the local workspace packages without network resolution. Add all required prefetch
inputs to both templates. Prove a network-disabled build on both architectures.
Do not weaken the hermetic flag to make a release build appear ready.
[Konflux hermetic build documentation](https://konflux-ci.dev/docs/building/hermetic-builds/)

## Integration tests and releases

Next implement a Kubernetes lifecycle for the existing smoke scenarios, using
image digests from the Snapshot and a fresh pod per invocation. A client sidecar
can reach the node's loopback gRPC listener. Use fake AAP/agent services for normal
CI and reliable cleanup on failures. The existing Docker smoke runner cannot run
unchanged in a Tekton pod without a container engine.

Register that pipeline as a required IntegrationTestScenario after it exists and
has been validated. Konflux integration tests reference a Git repository, revision
and pipeline path. [Integration test onboarding](https://konflux-ci.dev/docs/testing/integration/adding/)

Require the complete five-image snapshot to match the intended source revision
before release. Coordinate signing, SBOMs, policy checks, ReleasePlan/Admission,
destination repositories and digest inventory with release owners. The templates
do not configure these resources or guarantee those policies.

The development execution namespace `ep-dev-workers` is separate from the Konflux
build tenant. No cluster access or production credentials have been copied here.
