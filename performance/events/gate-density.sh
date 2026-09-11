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
GATE_QUIET="${GATE_QUIET:-false}"

gate_progress() {
  [[ "${GATE_QUIET}" == true ]] && return
  echo "$*" >&2
}

resolve_governance_ns 2>/dev/null || true

count() {
  local kind="$1"
  shift
  # shellcheck disable=SC2068
  oc get "${kind}" ${@} --no-headers 2>/dev/null | wc -l | tr -d ' '
}

count_policies() {
  local n
  gate_progress "Counting policies in ${GOVERNANCE_NS} ..."
  n="$(count policy.policy.open-cluster-management.io -n "${GOVERNANCE_NS}" -l "${REPRO_LABEL}")"
  if [[ "${n}" -eq 0 ]]; then
    n="$(count policy.policy.open-cluster-management.io -n "${GOVERNANCE_NS}")"
  fi
  gate_progress "  policies: ${n}"
  echo "${n}"
}

count_csrs() {
  local n
  gate_progress "Counting CSRs ..."
  n="$(count certificatesigningrequest.certificates.k8s.io -l "${REPRO_LABEL}")"
  if [[ "${n}" -eq 0 ]]; then
    n="$(oc get certificatesigningrequest.certificates.k8s.io -o name 2>/dev/null \
      | grep -cE "/${CLUSTER_PREFIX}-[0-9]+-csr$" || true)"
  fi
  gate_progress "  CSRs: ${n}"
  echo "${n}"
}

# Prefer label selector, then one cluster-wide list filtered by fleet namespace prefix.
count_labeled_or_fleet() {
  local kind="$1"
  local label="$2"
  local labeled n

  gate_progress "Counting ${label} ..."
  labeled="$(count "${kind}" -A -l "${REPRO_LABEL}")"
  if [[ "${labeled}" -gt 0 ]]; then
    gate_progress "  ${label}: ${labeled} (repro label)"
    echo "${labeled}"
    return
  fi

  n="$(count_fleet_namespace_column "${kind}" "${START}" "${END}")"
  gate_progress "  ${label}: ${n} (fleet namespaces ${CLUSTER_PREFIX}-*)"
  echo "${n}"
}

gate_progress "=== Density gate (indices ${START}-${END}) ==="
gate_progress "Listing fleet ManagedClusters ..."
MC="$(list_fleet_managedclusters "${START}" "${END}" | wc -l | tr -d ' ')"
gate_progress "  ManagedClusters: ${MC}"

MCA="$(count_labeled_or_fleet managedclusteraddon "ManagedClusterAddOn")"
POL="$(count_policies)"
CD="$(count_labeled_or_fleet clusterdeployments.hive.openshift.io "ClusterDeployment")"
MP="$(count_labeled_or_fleet machinepools.hive.openshift.io "MachinePool")"
CP="$(count_labeled_or_fleet clusterprovisions.hive.openshift.io "ClusterProvision")"

gate_progress "Counting PolicyReports ..."
PR="$(count policyreport -A -l "${REPRO_LABEL}")"
if [[ "${PR}" -eq 0 ]]; then
  PR="$(count_fleet_namespace_column policyreport.wgpolicyk8s.io "${START}" "${END}")"
fi
gate_progress "  PolicyReports: ${PR}"

CSR="$(count_csrs)"
SEC="$(count_labeled_or_fleet secret "Secret")"

pass=true
[[ "${MC}" -ge "${END}" ]] || pass=false
[[ "${MCA}" -ge $((END * 8)) ]] || pass=false
[[ "${POL}" -ge 3 ]] || pass=false
[[ "${CD}" -ge "${END}" ]] || pass=false
[[ "${MP}" -ge $((END * 2)) ]] || pass=false
[[ "${CP}" -ge "${END}" ]] || pass=false
[[ "${PR}" -ge "${END}" ]] || pass=false

echo "=== Density gate (indices ${START}-${END}) ==="
echo "GATE_A: MC=${MC} (need ${END})  MCA=${MCA} (need $((END * 8)))  POL=${POL} (need 3)"
echo "GATE_B: CD=${CD} (need ${END})  MP=${MP} (need $((END * 2)))  CP=${CP} (need ${END})  SEC=${SEC} (need ≥$((END * 5)))"
echo "GATE_D: PR=${PR} (need ${END})  CSR=${CSR} (optional, need ≥${END} if created)"
echo
if [[ "${pass}" == true ]]; then
  echo "Gate PASSED (CSR optional). Run: SSE_MAX_SECONDS=600 ./measure-sse.sh"
else
  echo "Gate INCOMPLETE — fix failing counts above, then ./measure-sse.sh"
  echo "Note: fleet counts use namespace prefix ${CLUSTER_PREFIX}-* when repro label is absent."
fi
echo
echo "Governance namespace: ${GOVERNANCE_NS}"
