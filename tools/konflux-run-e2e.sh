#!/usr/bin/env bash
# Run tests/e2e against AO and the Execution Plane on the Konflux Kind cluster.
#
# Intended for the Konflux step-types-e2e test task, after deploy-ao-using-aap-dev
# deployed the AO stack with AO_EXECUTION_PLANE=true and this repository cloned
# to ~/syntara-step-types (aap-dev's ensure-step-types-from-src.sh builds the
# script-node image from that checkout). Uses the AO ingress left by deploy-ao
# (https://127.0.0.1:8443) or a myao-backend port-forward, and the operator
# secret myao-initial-admin-password. Does not print secrets.
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/aap-dev/bin:${PATH}"

KUBECONFIG="${KUBECONFIG:-${HOME}/aap-dev/.tmp/27-next-ao-operator.kubeconfig}"
export KUBECONFIG

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
AO_NAMESPACE="${AO_NAMESPACE:-aap27-next}"
E2E_DIR="${REPO_ROOT}/tests/e2e"
RESULTS_DIR="${RESULTS_DIR:-${REPO_ROOT}/test-results}"

if [[ ! -f "${KUBECONFIG}" ]]; then
  echo "ERROR: kubeconfig not found: ${KUBECONFIG}" >&2
  exit 1
fi

_ao_admin_password() {
  kubectl get secret myao-initial-admin-password -n "${AO_NAMESPACE}" -o jsonpath='{.data.password}' | base64 -d
}

_ao_api_reachable() {
  local base=$1 code
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "${base}/api/v1/projects" || true)
  [[ -n "${code}" && "${code}" != "000" && "${code}" != "404" ]]
}

_ao_api_base() {
  local base="${AO_API_BASE:-https://127.0.0.1:8443}"
  if _ao_api_reachable "${base}"; then
    printf '%s' "${base}"
    return 0
  fi
  echo "ingress ${base} not reachable; port-forwarding myao-backend" >&2
  kubectl port-forward -n "${AO_NAMESPACE}" svc/myao-backend 18000:8000 >/tmp/ao-backend-pf.log 2>&1 &
  sleep 4
  printf '%s' "https://127.0.0.1:18000"
}

AO_API_BASE="$(_ao_api_base)"
export AO_API_BASE
# The aap-dev ingress serves a self-signed certificate.
export AO_API_CA_FILE="${AO_API_CA_FILE:-}"

_ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  echo "=== Installing uv ==="
  curl --proto "=https" -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
  command -v uv >/dev/null 2>&1
}

if ! _ensure_uv; then
  echo "ERROR: uv is required to run the dispatch e2e" >&2
  exit 1
fi

echo "==> Running syntara-step-types dispatch e2e against ${AO_API_BASE}"
mkdir -p "${RESULTS_DIR}"

AO_ADMIN_PASSWORD="$(_ao_admin_password)" \
  uv run --no-project --with "pytest>=8" --with "httpx>=0.28" -- \
    pytest "${E2E_DIR}" -v -m e2e \
    --junitxml="${RESULTS_DIR}/e2e-junit.xml"

echo "==> e2e results written to ${RESULTS_DIR}/e2e-junit.xml"
