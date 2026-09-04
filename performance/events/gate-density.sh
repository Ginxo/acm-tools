#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Gate counts for fleet + Hive (B) + governance (D) density layers.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

START="${1:-1}"
END="${2:-750}"

count() {
  local kind="$1"
  local extra="${2:-}"
  # shellcheck disable=SC2086
  oc get "${kind}" ${extra} --no-headers 2>/dev/null | wc -l | tr -d ' '
}

fleet_mc() {
  list_fleet_managedclusters "${START}" "${END}" | wc -l | tr -d ' '
}

MC="$(fleet_mc)"
MCA="$(count managedclusteraddon -A -l ${REPRO_LABEL})"
POL="$(count policy.policy.open-cluster-management.io -n ${GOVERNANCE_NS} -l ${REPRO_LABEL})"
CD="$(count clusterdeployments.hive.openshift.io -A -l ${REPRO_LABEL})"
MP="$(count machinepools.hive.openshift.io -A -l ${REPRO_LABEL})"
CP="$(count clusterprovisions.hive.openshift.io -A -l ${REPRO_LABEL})"
PR="$(count policyreport -A -l ${REPRO_LABEL})"
CSR="$(count csr -l ${REPRO_LABEL} 2>/dev/null || echo 0)"
SEC="$(count secret -A -l ${REPRO_LABEL})"

echo "=== Density gate (indices ${START}-${END}) ==="
echo "GATE_A: MC=${MC} (need ${END})  MCA=${MCA} (need $((END * 8)))  POL=${POL} (need 3)"
echo "GATE_B: CD=${CD} (need ${END})  MP=${MP} (need $((END * 2)))  CP=${CP} (need ${END})  SEC=${SEC} (need ≥$((END * 5)))"
echo "GATE_D: PR=${PR} (need ${END})  CSR=${CSR} (need ≥${END})"
echo
echo "Run ./measure-sse.sh after the gate passes to measure decompressed /multicloud/events SSE size."
