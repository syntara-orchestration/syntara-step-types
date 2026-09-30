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

## Provenance

Extracted from `syntara-orchestration/syntara`, commit
`8ddd73e5c65ab8d4f6072d0a2bdc78bc59faf265`, under `backend/nodes`.
The SDK revision is pinned in `pyproject.toml`. The upstream Apache 2.0 LICENSE
and applicable source attribution are retained. NOTICE records relevant upstream
notices; `uv.lock` records this workspace's dependency versions.
