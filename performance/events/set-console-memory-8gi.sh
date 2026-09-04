#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Raise ACM console backend pod memory limit to 8Gi (customer repro baseline).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc
resolve_console_deploy

MEMORY_LIMIT="${MEMORY_LIMIT:-8Gi}"
MEMORY_REQUEST="${MEMORY_REQUEST:-512Mi}"

echo "Deployment: ${CONSOLE_DEPLOY} (namespace ${CONSOLE_NS})"
echo "Excluded sidecar: acm-cli-downloads (component=console but not the Node backend)"
echo "Current resources:"
oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}' || true
echo

# Patch memory only — do not raise cpu requests above existing cpu limits.
oc set resources deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  --limits=memory="${MEMORY_LIMIT}" \
  --requests=memory="${MEMORY_REQUEST}"

echo "Waiting for rollout..."
oc rollout status deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}"

echo "Updated resources:"
oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'

resolve_console_pod
if [[ -n "${CONSOLE_POD:-}" ]]; then
  echo
  echo "Pod: ${CONSOLE_POD}"
  oc describe pod "${CONSOLE_POD}" -n "${CONSOLE_NS}" | grep -E 'Limits:|Requests:' || true
else
  echo "warning: no running pod found for ${CONSOLE_DEPLOY}" >&2
fi
