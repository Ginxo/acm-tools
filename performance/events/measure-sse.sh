#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Measure console SSE — direct port-forward (default) or OCP console plugin proxy.

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

if [[ "${MODE}" == "proxy" ]]; then
  resolve_console_events_url
  args+=(--url "${CONSOLE_EVENTS_URL}")
  echo "Mode: console plugin proxy"
  echo "Events URL: ${CONSOLE_EVENTS_URL}"
  echo "Note: proxy often requires a browser session cookie; use SSE_MODE=direct if you get HTTP 401/403."
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
