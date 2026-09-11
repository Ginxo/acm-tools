#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Measure console SSE — direct (hub pod), local dev backend, or OCP console proxy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

MAX_SECONDS="${SSE_MAX_SECONDS:-600}"
SAVE="${SSE_SAVE:-}"
MODE="${SSE_MODE:-direct}"
LOCAL_PORT="${SSE_LOCAL_PORT:-19443}"
LOCAL_BACKEND_PORT="${LOCAL_BACKEND_PORT:-4000}"
CONSOLE_PORT="${CONSOLE_PORT:-9000}"
MCE_PORT="${MCE_PORT:-3002}"
PF_PID=""

cleanup() {
  if [[ -n "${PF_PID}" ]]; then
    kill "${PF_PID}" 2>/dev/null || true
    wait "${PF_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

args=(--max-seconds "${MAX_SECONDS}")
if [[ -n "${SAVE}" ]]; then
  mkdir -p "$(dirname "${SAVE}")"
  args+=(--save "${SAVE}")
fi

if [[ "${MODE}" == "local" ]]; then
  LOCAL_URL="${SSE_LOCAL_URL:-https://127.0.0.1:${LOCAL_BACKEND_PORT}/events}"
  args+=(--url "${LOCAL_URL}")
  echo "Mode: local dev backend only (direct /events — not the browser URL path)"
  echo "Events URL: ${LOCAL_URL}"
  echo "Prerequisites: oc login to hub, backend/.env from npm run setup, certs in backend/certs"
elif [[ "${MODE}" == "plugins" ]]; then
  resolve_plugins_events_url
  args+=(--url "${CONSOLE_EVENTS_URL}")
  echo "Mode: npm run plugins — local OpenShift Console plugin proxy (browser-like)"
  echo "Events URL: ${CONSOLE_EVENTS_URL}"
  echo "Prerequisites: npm run plugins running (backend + ocp-console on :${CONSOLE_PORT})"
  echo "Auth: oc Bearer token by default; if HTTP 401 set SSE_COOKIE from browser DevTools"
elif [[ "${MODE}" == "plugin-dev" ]]; then
  PLUGIN_URL="${SSE_PLUGIN_URL:-https://127.0.0.1:${MCE_PORT}/multicloud/events}"
  args+=(--url "${PLUGIN_URL}")
  echo "Mode: MCE webpack dev server proxy (/multicloud/events → backend)"
  echo "Events URL: ${PLUGIN_URL}"
  echo "Prerequisites: npm run plugins (frontend on :${MCE_PORT}), oc login"
elif [[ "${MODE}" == "proxy" ]]; then
  resolve_console_events_url
  args+=(--url "${CONSOLE_EVENTS_URL}")
  echo "Mode: OpenShift Console plugin proxy (browser-like URL path)"
  echo "Events URL: ${CONSOLE_EVENTS_URL}"
  echo "Note: uses oc Bearer token; browser uses session cookie — sizes should match, latency may differ."
else
  resolve_console_deploy
  CONTAINER_PORT="$(resolve_console_container_port)"
  CONTAINER_PORT="${CONSOLE_BACKEND_PORT:-${CONTAINER_PORT}}"

  echo "Mode: direct (port-forward to console backend)"
  echo "Deployment: ${CONSOLE_DEPLOY} (ns ${CONSOLE_NS}, container port ${CONTAINER_PORT})"

  oc port-forward -n "${CONSOLE_NS}" "deploy/${CONSOLE_DEPLOY}" \
    "${LOCAL_PORT}:${CONTAINER_PORT}" >/dev/null 2>&1 &
  PF_PID=$!
  sleep 3

  DIRECT_URL="https://127.0.0.1:${LOCAL_PORT}/events"
  args+=(--url "${DIRECT_URL}")
  echo "Events URL: ${DIRECT_URL} (falls back to /multicloud/events on 404)"
fi

echo "Measuring until LOADED or ${MAX_SECONDS}s ..."
python3 "${SCRIPT_DIR}/measure-sse.py" "${args[@]}" "$@"
