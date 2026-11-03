#!/bin/bash
# Helper script to test the Python and Bash containers via grpcurl

set -e

PROTO_PATH=".venv/lib/python3.12/site-packages/syntara_plugin/contracts/proto"
PROTO_FILE="syntara_plugin/runtime/protocol/container.proto"
PYTHON_HOST="localhost:50051"
BASH_HOST="localhost:50052"
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
  "$PYTHON_HOST" \
  "$SERVICE/Health"
grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  "$BASH_HOST" \
  "$SERVICE/Health"
echo ""

# Test 2: Python Print
echo "Test 2: Python Print"
echo "===================="
INPUTS=$(encode_json '{"code": "print(\"Hello from grpcurl!\")"}')
RUNTIME_CTX=$(encode_json '{}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-1\",
    \"workflow_run_id\": \"wf-grpc-1\",
    \"step_revision_id\": \"syntara.script-python.script-python\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$PYTHON_HOST" \
  "$SERVICE/Execute"
echo ""

# Test 3: Bash Echo
echo "Test 3: Bash Echo"
echo "================="
INPUTS=$(encode_json '{"code": "echo \"Hello from bash!\""}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-2\",
    \"workflow_run_id\": \"wf-grpc-2\",
    \"step_revision_id\": \"syntara.script-bash.script-bash\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$BASH_HOST" \
  "$SERVICE/Execute"
echo ""

# Test 4: Python with JSON output
echo "Test 4: Python JSON Output"
echo "=========================="
INPUTS=$(encode_json '{"code": "import json; print(json.dumps({\"status\": \"success\", \"value\": 42}))"}')

grpcurl -plaintext \
  -import-path "$PROTO_PATH" \
  -proto "$PROTO_FILE" \
  -d "{
    \"invocation_id\": \"grpc-test-3\",
    \"workflow_run_id\": \"wf-grpc-3\",
    \"step_revision_id\": \"syntara.script-python.script-python\",
    \"runtime_abi\": \"syntara.container/v1alpha1\",
    \"inputs_json\": \"$INPUTS\",
    \"runtime_context_json\": \"$RUNTIME_CTX\",
    \"timeout_seconds\": 60
  }" \
  "$PYTHON_HOST" \
  "$SERVICE/Execute"

echo ""
echo "All tests completed!"
