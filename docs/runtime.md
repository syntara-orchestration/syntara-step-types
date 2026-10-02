# Runtime and gRPC contract

The `syntara.node.v1.NodeService` contract is defined in
[`node.proto`](../_protocol/src/syntara_node_protocol/node.proto). The shared
server loads a node class using `SYNTARA_NODE_MODULE` and listens on port 50051.

| RPC | Behavior |
| --- | --- |
| Health | Readiness and protocol version before admission |
| Execute | One invocation; streams progress followed by one terminal SDK result |
| Cancel | Cooperatively cancels the active invocation |

Inputs, resolved credentials, execution context and settings occupy separate
protobuf fields. Dynamic values use JSON bytes to preserve large integers and
nulls. Requests and responses have a 2 MiB transport limit. Outputs retain the
SDK StandardOutputWrapper fields and typed error/retry information. Logs use
stderr; stdin/stdout are not the server's control transport.

Each server instance accepts one invocation. A second Execute is rejected, even
after ambiguous delivery. Callers must not automatically resubmit an uncertain
request: external work may already have started. Progress and returned values
are redacted using the supplied resolved secrets and secret-valued runtime
settings. SDK logger messages receive the same redaction, and traceback details
are suppressed. Invocation timeouts are capped at 24 hours. Cancellation, RPC
deadlines, disconnects and SIGTERM request cooperative cleanup by the step
implementation. The execution infrastructure owns container creation, resource
limits and removal.

## Connections and TLS

The default listener is `127.0.0.1:50051`. A client sharing the container's network
namespace, or a Kubernetes port-forward connection, can reach it. Infrastructure
credentials and Kubernetes RBAC belong to the caller's deployment.

Direct connections on a non-loopback listener require mutual TLS:

```dotenv
SYNTARA_NODE_GRPC_ADDRESS=0.0.0.0:50051
SYNTARA_NODE_GRPC_TLS_CERT=/run/grpc-tls/tls.crt
SYNTARA_NODE_GRPC_TLS_KEY=/run/grpc-tls/tls.key
SYNTARA_NODE_GRPC_TLS_CA=/run/grpc-tls/ca.pem
```

Mount the server identity and client CA using the execution platform's secret
mechanism. The client must trust the server and present an accepted certificate.
Non-loopback listeners reject startup without TLS.

## Client and generated code

With a server or forwarded port available, send an invocation from a local file:

```bash
uv run --frozen --all-packages python -m syntara_node_protocol \
  --address 127.0.0.1:50051 --file /path/to/invocation.json
```

For direct TLS, add `--ca`, `--cert` and `--key`. The CLI prints progress and
terminal results for inspection. Protect invocation files containing credentials.

Generated Python clients, server bindings and type stubs are checked in. Run
`make proto` after editing the protocol; CI detects generated-file drift. Syntara
must consume a versioned protocol package or immutable Git revision when replacing
its current local dependency on `nodes/_protocol`.
