#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Hub preflight checks before loading mock fleet data.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc
resolve_console_deploy

echo "=== Hub ==="
oc whoami --show-server
oc whoami
echo

echo "=== ACM MultiClusterHub ==="
oc get mch -n open-cluster-management 2>/dev/null || echo "(mch CR not found)"
echo

echo "=== Console backend deployment (excludes acm-cli-downloads) ==="
echo "Selected: ${CONSOLE_DEPLOY} (ns ${CONSOLE_NS})"
echo "Other candidates:"
list_console_deploy_candidates "${CONSOLE_NS}" | grep -v "^${CONSOLE_DEPLOY}$" || echo "  (none)"
oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  -o custom-columns=NAME:.metadata.name,REPLICAS:.spec.replicas,IMAGE:.spec.template.spec.containers[0].image
echo "Memory resources:"
oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'
echo

echo "=== Console pod (if running) ==="
resolve_console_pod 2>/dev/null || true
if [[ -n "${CONSOLE_POD:-}" ]]; then
  oc adm top pod -n "${CONSOLE_NS}" "${CONSOLE_POD}" --containers 2>/dev/null || \
    echo "(metrics-server not available — skip top)"
else
  echo "(no running console backend pod yet)"
fi
echo

echo "=== Inventory baseline ==="
echo -n "ManagedCluster: "
oc get managedcluster --no-headers 2>/dev/null | wc -l
echo -n "ManagedClusterAddOn (all ns): "
oc get managedclusteraddon -A --no-headers 2>/dev/null | wc -l
echo -n "Policy (all ns): "
oc get policy -A --no-headers 2>/dev/null | wc -l
echo -n "Repro clusters (${REPRO_LABEL}): "
oc get managedcluster -l "${REPRO_LABEL}" --no-headers 2>/dev/null | wc -l
echo

echo "Next steps:"
echo "  1. ./set-console-memory-8gi.sh"
echo "  2. oc set env deployment/${CONSOLE_DEPLOY} -n ${CONSOLE_NS} LOG_MEMORY=true LOG_LEVEL=info"
echo "  3. ./apply-batch.sh --start 1 --end 10"
