#!/bin/bash
# Helper script to test script container via grpcurl

set -e

PROTO_PATH="../syntara-plugin-sdk/contracts/proto"
PROTO_FILE="syntara_plugin/runtime/protocol/container.proto"
HOST="localhost:50051"
SERVICE="syntara.container.v1alpha1.ContainerService"

# Function to encode JSON to base64
encode_json() {
    echo -n "$1" | base64
}

# Test 1: Health Check
echo "Test 1: Health Check"
echo "===================="
grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  "$HOST" \
  "$SERVICE/Health"
echo ""

# Test 2: Python Print
echo "Test 2: Python Print"
echo "===================="
INPUTS=$(encode_json '{"language": "python", "code": "print(\"Hello from grpcurl!\")"}')
RUNTIME_CTX=$(encode_json '{}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-1\",
    \"workflow_run_id\": \"wf-grpc-1\",
    \"step_revision_id\": \"syntara.step.script:0.1.0\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$HOST" \
  "$SERVICE/Execute"
echo ""

# Test 3: Bash Echo
echo "Test 3: Bash Echo"
echo "================="
INPUTS=$(encode_json '{"language": "bash", "code": "echo \"Hello from bash!\""}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-2\",
    \"workflow_run_id\": \"wf-grpc-2\",
    \"step_revision_id\": \"syntara.step.script:0.1.0\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$HOST" \
  "$SERVICE/Execute"
echo ""

# Test 4: Python with JSON output
echo "Test 4: Python JSON Output"
echo "=========================="
INPUTS=$(encode_json '{"language": "python", "code": "import json; print(json.dumps({\"status\": \"success\", \"value\": 42}))"}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-3\",
    \"workflow_run_id\": \"wf-grpc-3\",
    \"step_revision_id\": \"syntara.step.script:0.1.0\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$HOST" \
  "$SERVICE/Execute"

echo ""
echo "All tests completed!"
