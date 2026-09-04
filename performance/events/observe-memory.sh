#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Monitor ACM console backend pod memory during UI repro.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc
resolve_console_deploy
resolve_console_pod

INTERVAL="${INTERVAL:-10}"
MEMORY_LIMIT_MIB="${MEMORY_LIMIT_MIB:-8192}"

if [[ -z "${CONSOLE_POD:-}" ]]; then
  echo "error: no running pod for deployment ${CONSOLE_DEPLOY}" >&2
  exit 1
fi

echo "Console deployment: ${CONSOLE_DEPLOY} (ns ${CONSOLE_NS})"
echo "Pod: ${CONSOLE_POD}"
echo "Memory limit reference: ${MEMORY_LIMIT_MIB} MiB (8 GiB)"
echo "Interval: ${INTERVAL}s — Ctrl+C to stop"
echo

while true; do
  date -Is
  echo "--- oc adm top ---"
  selector="$(oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
    -o go-template='{{range $k,$v := .spec.selector.matchLabels}}{{$k}}={{$v}},{{end}}' \
    | sed 's/,$//')"
  if ! oc adm top pod -n "${CONSOLE_NS}" -l "${selector}" --containers 2>/dev/null; then
    echo "(metrics-server unavailable)"
  fi

  echo "--- resources / limits ---"
  oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
    -o jsonpath='limits.memory={.spec.template.spec.containers[0].resources.limits.memory}{"\n"}' 2>/dev/null || true

  echo "--- /proc/1/status (RSS) ---"
  oc exec -n "${CONSOLE_NS}" "${CONSOLE_POD}" -- sh -c \
    'grep -E "^(VmRSS|VmSize|VmPeak):" /proc/1/status' 2>/dev/null || echo "(exec failed — pod restarting?)"

  echo "--- last LOG_MEMORY line ---"
  oc logs -n "${CONSOLE_NS}" deployment/"${CONSOLE_DEPLOY}" --since="${INTERVAL}s" 2>/dev/null \
    | grep '"msg":"memory"' | tail -1 || echo "(none)"

  echo "=========================="
  sleep "${INTERVAL}"
done
