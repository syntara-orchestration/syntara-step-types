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

_secret_to_file() {
  local secret=$1 namespace=$2 key=$3 dest=$4
  kubectl get secret "${secret}" -n "${namespace}" -o json | python3 -c "
import base64, json, sys
secret = json.load(sys.stdin)
key = sys.argv[1]
dest = sys.argv[2]
data = secret.get('data') or {}
if key not in data:
    raise SystemExit(f'secret is missing key {key}')
open(dest, 'wb').write(base64.b64decode(data[key]))
" "${key}" "${dest}"
  chmod 600 "${dest}"
}

_ep_api_reachable() {
  local base=$1 code
  code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "${base}/healthz/ready" || true)
  [[ "${code}" == "200" || "${code}" == "204" ]]
}

_ep_api_url() {
  local base="${EP_API_URL:-}"
  if [[ -n "${base}" ]] && _ep_api_reachable "${base}"; then
    printf '%s' "${base}"
    return 0
  fi
  echo "port-forwarding ${AO_NAMESPACE}/svc/myao-execution-plane-api to 127.0.0.1:18443" >&2
  kubectl port-forward -n "${AO_NAMESPACE}" svc/myao-execution-plane-api 18443:8443 >/tmp/ep-api-pf.log 2>&1 &
  sleep 4
  printf '%s' "https://127.0.0.1:18443"
}

# Direct Execution Plane dispatch for the plugin images: the AO JWT private
# key (operator-generated), the EP API, the EP checkout's submit tool, and
# the plugin image references aap-dev recorded at build time.
EP_JWT_FILE="${EP_AO_JWT_PRIVATE_KEY_FILE:-/tmp/step-types-e2e-jwt-primary.pem}"
_secret_to_file myao-jwt-primary "${AO_NAMESPACE}" jwt-primary.pem "${EP_JWT_FILE}"
export EP_AO_JWT_PRIVATE_KEY_FILE="${EP_JWT_FILE}"
export EP_API_URL="$(_ep_api_url)"
export EP_TOOLS_DIR="${EP_TOOLS_DIR:-${HOME}/syntara-execution-plane}"

AAP_DEV_TMP="${HOME}/aap-dev/.tmp"
for plugin in script-python script-bash; do
  ref_file="${AAP_DEV_TMP}/ao-sdk-script-step-${plugin}-image.txt"
  if [[ -f "${ref_file}" ]]; then
    export SYNTARA_NODE_$(echo "${plugin}" | tr 'a-z-' 'A-Z_')_IMAGE="$(tr -d '\r\n' < "${ref_file}")"
  else
    echo "==> No ${plugin} plugin image reference at ${ref_file}; its dispatch test will skip"
  fi
done

echo "==> Running syntara-step-types dispatch e2e (AO: ${AO_API_BASE}, EP: ${EP_API_URL})"
mkdir -p "${RESULTS_DIR}"

AO_ADMIN_PASSWORD="$(_ao_admin_password)" \
  uv run --no-project --with "pytest>=8" --with "httpx>=0.28" -- \
    pytest "${E2E_DIR}" -v -m e2e \
    --junitxml="${RESULTS_DIR}/e2e-junit.xml"

echo "==> e2e results written to ${RESULTS_DIR}/e2e-junit.xml"
