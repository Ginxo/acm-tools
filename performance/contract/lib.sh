#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Shared helpers for contract performance benchmarks.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONTRACT_BACKEND_URL="${CONTRACT_BACKEND_URL:-https://localhost:4000}"
CONTRACT_PATH_PREFIX="${CONTRACT_PATH_PREFIX:-}"
CONTRACT_TLS_INSECURE="${CONTRACT_TLS_INSECURE:-true}"
CONTRACT_HTTP_TIMEOUT="${CONTRACT_HTTP_TIMEOUT:-60}"
CONTRACT_SSE_TIMEOUT="${CONTRACT_SSE_TIMEOUT:-120}"
LOCAL_BACKEND_PORT="${LOCAL_BACKEND_PORT:-4000}"

load_env() {
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
  fi
}

require_oc() {
  if ! command -v oc >/dev/null 2>&1; then
    echo "error: oc not found in PATH" >&2
    exit 1
  fi
  if ! oc whoami >/dev/null 2>&1; then
    echo "error: not logged in — run: oc login ..." >&2
    exit 1
  fi
}

contract_token() {
  if [[ -n "${CONTRACT_TOKEN:-}" ]]; then
    echo "${CONTRACT_TOKEN}"
    return
  fi
  oc whoami -t
}

preflight_backend() {
  local token url
  token="$(contract_token)"
  url="${CONTRACT_BACKEND_URL%/}/ping"
  if [[ -n "${CONTRACT_PATH_PREFIX}" ]]; then
    url="${CONTRACT_BACKEND_URL%/}${CONTRACT_PATH_PREFIX}/ping"
  fi
  if ! curl -sk -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${token}" "${url}" | grep -q '^200$'; then
    echo "error: backend not reachable at ${CONTRACT_BACKEND_URL} (GET /ping failed)" >&2
    echo "hint: start npm run plugins and ensure backend listens on :${LOCAL_BACKEND_PORT}" >&2
    exit 1
  fi
}

case_count() {
  python3 "${SCRIPT_DIR}/load_catalog.py" --catalog-dir "${SCRIPT_DIR}/catalog" ${CONTRACT_GROUP:+--group "${CONTRACT_GROUP}"} 2>&1 \
    | awk '/^cases=/{print $1}' | cut -d= -f2
}
