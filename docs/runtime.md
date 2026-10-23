# Runtime and gRPC contract

Step containers implement the `syntara.container.v1alpha1` ABI defined in the
`syntara-plugin-runtime` SDK (`ContainerService` proto). Each container exposes
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

Each step is registered under a `step_identity` (e.g. `syntara.step.script:0.1.0`).
The Execution Plane sends this string as `step_revision_id` in `ExecuteRequest`.
The container's `StepRegistry` routes the request to the matching handler.

## Connections and TLS

The default listener is `0.0.0.0:50051`. For development, connect directly or via
`kubectl port-forward`. Production deployments should use mutual TLS; configure via
the Execution Plane's deployment manifests.

## Failure codes

Steps raise `ContainerInvocationError(FailureCode.*, ...)` for controlled failures:

| Code | Meaning | Retryable |
| --- | --- | --- |
| `REMOTE_REJECTED` | Script non-zero exit; HTTP 4xx | No |
| `UPSTREAM_TRANSIENT` | HTTP 429/502/503/504; network error | Yes |
| `TIMEOUT` | Script or HTTP request timed out | Yes |
| `POLICY_DENIED` | SSRF validation blocked the URL | No |
| `INVALID_INPUT` | Input failed Pydantic model validation | No |

## Building and running locally

```bash
# Build both images (from repo root)
make node-images CONTAINER_ENGINE=docker TAG=local-test

# Run the script step container
docker run --rm -p 50051:50051 localhost/syntara-node-script:local-test
```

Inspect health or send an invocation using any gRPC client pointed at `localhost:50051`
with the `syntara-plugin-runtime` proto (`container.proto`).
