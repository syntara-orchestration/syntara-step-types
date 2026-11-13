# Runtime and gRPC contract

Step containers implement the `syntara.container.v1alpha1` ABI defined in the
`syntara-plugin-contracts` package (`ContainerService` proto). Each container exposes
a gRPC server on port 50051 and can serve multiple concurrent invocations.

| RPC | Behaviour |
| --- | --- |
| Health | Reports readiness and the ABI version; lists registered step revision IDs |
| Execute | Bidirectional stream: the client opens with a `start` request and may answer credential requests; the server sends zero or more `Progress` events and exactly one `Result` or `Failure` |
| Cancel | Requests cooperative cancellation of an active invocation |

Inputs arrive as JSON bytes (`inputs_json`). Runtime context (e.g.
`outputSchemaDigest`) is carried in `runtime_context_json`; it never contains an
endpoint or credential. Large integers and null values are preserved by the JSON
encoding.

Credentials are exchanged on the Execute stream; the request itself carries none
(the pre-a4 `credential_file_paths_json` field is reserved). When a step declares
credentials, the server sends a `credential_request` naming the declared recipe,
and the client answers with a `credential_response` holding either a JSON document
or a `CREDENTIAL_ERROR_*` outcome. The response is sensitive and never appears as
an execution event. A client may close its send stream; the invocation continues,
but pending and future credential reads fail. Any other client message before the
terminal event cancels the invocation with a terminal `CANCELLED` failure.

## Step identity and revision

Each step has a static identity `namespace.plugin.step`
(for example `syntara.script-python.script-python`), taken from plugin metadata
and the Action `metadata.name`. The target version is not appended to that identity.

Health and Execute use the compiled revision: the `sha256:` digest of the compiled
release. Both require that exact digest. The static identity is not a revision,
and `*` is not an Execute value.

Images that still register through `step_registry_from_bindings` advertise the
static identity on Health. That listing is not the compiled revision. Cutover
validation uses the digest from the registration manifest once the image runs
the SDK server path.

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
# Build all workload images (from repo root)
make node-images CONTAINER_ENGINE=docker TAG=local-test

# Run a Python script step container
docker run --rm -p 50051:50051 localhost/syntara-node-script-python:local-test
```

Inspect health or send an invocation using any gRPC client pointed at `localhost:50051`
with the `syntara-plugin-contracts` proto (`container.proto`).
