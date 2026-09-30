# Source provenance

The initial import contains the tracked `backend/nodes` workspace from
`https://github.com/syntara-orchestration/syntara`, branch
`feat/sdk-node-containers`, commit `8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265`.
The machine-specific `LOCAL-OPENSHIFT.md` runbook was excluded. No local environment
files, credentials, virtual environments or Syntara backend services were copied.

The original LICENSE and NOTICE are preserved. NOTICE describes the original
monorepo dependency inventory; it is not an SBOM of these five images. Generate
image-specific SBOMs and review notices before a public release.

The gRPC protocol is version 1 (`syntara.node.v1`); the protocol Python package is
version 0.2.0. The SDK source revision is pinned in `pyproject.toml` and `uv.lock`.

Workflow dispatch, Temporal integration and Syntara model parity tests remain in
the source repository. Moving the images does not migrate those consumer pieces.
