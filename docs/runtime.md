# Runtime and gRPC contract

Step containers implement the `syntara.container.v1alpha1` ABI defined in the
`syntara-plugin-contracts` package (`ContainerService` proto). Each container exposes
a gRPC server on port 50051 and can serve multiple concurrent invocations.

| RPC | Behaviour |
| --- | --- |
| Health | Reports readiness and the ABI version; lists registered step revision IDs |
| Execute | Streams zero or more `Progress` events followed by exactly one `Result` or `Failure` |
| Cancel | Requests cooperative cancellation of an active invocation |

Inputs arrive as JSON bytes (`inputs_json`). Credential file paths are passed as
a JSON map (`credential_file_paths_json`) referencing files mounted by the Execution
Plane at `/var/run/syntara/secrets/`. Runtime context (e.g. `outputSchemaDigest`) is
carried in `runtime_context_json`. Large integers and null values are preserved by
the JSON encoding.

## Step identity

Each step is registered under the identity produced by its compiled plugin target
(e.g. `syntara.script.script`). The target version is carried separately and is
not appended to this identity.
The Execution Plane sends this string as `step_revision_id` in `ExecuteRequest`.
The container's `StepRegistry` routes the request to the matching handler.

## Connections and TLS

The default listener is `0.0.0.0:50051`. For development, connect directly or via
`kubectl port-forward`. Production deployments should use mutual TLS; configure via
the Execution Plane's deployment manifests.

## Failure results

Steps raise `StepFailure` for deliberate, controlled failures. The runtime sends these
as a `Result` with a nonzero `status_code` and structured `error_result_json`. The
`Failure` event is reserved for runtime or protocol failures.

| Code | Category | Meaning | Retryable |
| --- | --- | --- | --- |
| `REMOTE_REJECTED` | `BUSINESS` | Script non-zero exit; HTTP 4xx | No |
| `UPSTREAM_TRANSIENT` | `DEPENDENCY` | HTTP 429/502/503/504; network error | Yes |
| `TIMEOUT` | `TIMEOUT` | Script or HTTP request timed out | Yes |
| `POLICY_DENIED` | `POLICY` | SSRF validation blocked the URL | No |
| `CANCELLED` | `CANCELLED` | Invocation cancelled; script process group or HTTP I/O stopped | No |
| `INVALID_INPUT` | `VALIDATION` | Input failed Pydantic model validation | No |

## Building and running locally

```bash
# Build both images (from repo root)
make node-images CONTAINER_ENGINE=docker TAG=local-test

# Run the script step container
docker run --rm -p 50051:50051 localhost/syntara-node-script:local-test
```

Inspect health or send an invocation using any gRPC client pointed at `localhost:50051`
with the `syntara-plugin-contracts` proto (`container.proto`).
