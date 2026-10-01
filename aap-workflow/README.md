# aap-workflow SDK node

This folder produces `syntara-node-aap-workflow`. Its entrypoint starts a gRPC server
on port 50051. See [the runtime and deployment guide](../docs/runtime.md) for the protobuf
contract, port-forwarding, mutual TLS, cancellation and workflow compatibility.

From the repository root:

```bash
make node-image NODE=aap-workflow REGISTRY=quay.io/your-organization TAG=grpc-migration
```

With a running pod and its gRPC port forwarded to localhost:

```bash
uv run --frozen --all-packages python -m syntara_node_protocol \
  --address 127.0.0.1:50051 --file aap-workflow/example.json
```

Set real URLs and reference IDs in a protected copy of `example.json`; never commit
credentials. The client sends protobuf requests and receives a gRPC response stream.
The pod accepts one invocation and exits after returning its result. Logs go to
stderr.

The container launches and monitors a workflow in the configured AAP instance.
Its child jobs run in AAP's execution environments. Inputs and credentials use
the invocation contract described in the runtime guide.

Build this folder's `Containerfile` with **the repository root as the build context**.
