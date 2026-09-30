# Local preparation validation

Validated on 30 September 2026 using uv 0.12.3, macOS ARM64 and the local Linux
ARM64 Docker engine. These are local results; hosted GitHub/Konflux runs require
upstream activation.

| Check | Result |
| --- | --- |
| Frozen workspace install | Passed |
| Ruff lint and formatting | Passed |
| Strict mypy | Passed, 26 source files |
| Generated protobuf/stub and requirements drift | Passed |
| Python 3.12.8 | 48 tests passed; JUnit and coverage reports generated |
| Python 3.13.5 | 48 tests passed |
| Python 3.14.0 | 48 tests passed |
| Five Containerfile builds | Passed, Linux ARM64, tag `staging-ci` |
| Built-image gRPC smoke | HTTP, Python, Bash, script failure, agent, AAP job and AAP workflow passed |
| GitHub workflow | Actionlint 1.7.7 passed |
| Ten Tekton templates | Parsed; build contexts and Containerfile paths verified |
| Relative Markdown links and Git whitespace | Passed |

The smoke images were built from the preparation working tree before its commit,
so they are local test artifacts, not release images. Smoke containers and their
temporary network were removed. The five local images remain available.

The initial Python 3.14 lookup selected an existing 3.14 beta interpreter and failed
during dependency import. Rerunning with stable 3.14.0 passed without runtime code
changes. GitHub Actions selects stable versions through setup-python.

The extended Bash smoke assertion was adjusted to the existing contract: Bash
returns raw stdout and does not populate Python's `stdout_json` field. No executor
business logic was changed for this staging preparation.

Still to validate during onboarding: GitHub-hosted Linux AMD64 CI, hermetic builds,
production AMD64/ARM64 Konflux builds, snapshot-driven Kubernetes integration
tests, live AAP child-workflow tests, release policy checks, and the Syntara
consumer's external protocol dependency. See [the plan](ci-plan.md).
