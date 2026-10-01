# agent SDK node

This folder produces `syntara-node-agent`. Its entrypoint starts a gRPC server
on port 50051. See [the runtime and deployment guide](../docs/runtime.md) for the protobuf
contract, port-forwarding, mutual TLS, cancellation and workflow compatibility.

From the repository root:

```bash
make node-image NODE=agent REGISTRY=quay.io/your-organization TAG=grpc-migration
```

With a running pod and its gRPC port forwarded to localhost:

```bash
uv run --frozen --all-packages python -m syntara_node_protocol \
  --address 127.0.0.1:50051 --file agent/example.json
```

Set real URLs and reference IDs in a protected copy of `example.json`; never commit
credentials. The client sends protobuf requests and receives a gRPC response stream.
The pod accepts one invocation and exits after returning its result. Logs go to
stderr.

Execution returns the agent invocation acknowledgement. Agent Orchestrator's
existing callback completes the workflow activity; the acknowledgement alone is
not the final agent result. The `CANCEL_EXTERNAL` operation requests cancellation
of previously submitted invocations.

Build this folder's `Containerfile` with **the repository root as the build context**.
