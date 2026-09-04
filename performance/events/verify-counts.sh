#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Print inventory counts for repro resources.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc 2>/dev/null || true

if ! oc whoami >/dev/null 2>&1; then
  echo "not logged in"
  exit 1
fi

count() {
  local kind="$1"
  local extra="${2:-}"
  # shellcheck disable=SC2086
  oc get "${kind}" ${extra} --no-headers 2>/dev/null | wc -l | tr -d ' '
}

count_mci_for_fleet() {
  local n=0
  local mc
  while read -r mc; do
    [[ -n "${mc}" ]] || continue
    if oc get managedclusterinfo "${mc}" -n "${mc}" >/dev/null 2>&1; then
      n=$((n + 1))
    fi
  done < <(list_fleet_managedclusters 1 9999)
  echo "${n}"
}

fleet_mc_count() {
  list_fleet_managedclusters 1 9999 | wc -l | tr -d ' '
}

echo "=== Mock fleet counts (${REPRO_LABEL}) ==="
echo "ManagedCluster (label):   $(count managedcluster -l "${REPRO_LABEL}")"
echo "ManagedCluster (prefix):  $(fleet_mc_count)"
echo "ManagedClusterInfo:       $(count_mci_for_fleet)"
echo "ManagedClusterAddOn:      $(count managedclusteraddon -A -l "${REPRO_LABEL}")"
echo "Policy (governance ns): $(count policy.policy.open-cluster-management.io -n "${GOVERNANCE_NS}" -l "${REPRO_LABEL}")"
echo
echo "=== Layer B (Hive) ==="
echo "ClusterDeployment:        $(count clusterdeployments.hive.openshift.io -A -l "${REPRO_LABEL}")"
echo "MachinePool:              $(count machinepools.hive.openshift.io -A -l "${REPRO_LABEL}")"
echo "ClusterProvision:         $(count clusterprovisions.hive.openshift.io -A -l "${REPRO_LABEL}")"
echo "Secrets (repro label):    $(count secret -A -l "${REPRO_LABEL}")"
echo
echo "=== Layer D (governance) ==="
echo "PolicyReport:             $(count policyreport -A -l "${REPRO_LABEL}")"
echo "CSR (repro label):        $(count csr -l "${REPRO_LABEL}" 2>/dev/null || echo 0)"
echo
echo "=== Hub totals ==="
echo "ManagedCluster (all):     $(count managedcluster)"
echo "ManagedClusterAddOn (all): $(count managedclusteraddon -A)"
echo "Policy (all ns):          $(count policy.policy.open-cluster-management.io -A)"
