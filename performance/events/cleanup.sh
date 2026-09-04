#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Remove labeled mock fleet and governance resources from the hub.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

FORCE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=true; shift ;;
    -h|--help)
      echo "Usage: $(basename "$0") [--force]"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

echo "Resources to delete (label ${REPRO_LABEL}):"
list_fleet_managedclusters 1 9999 | wc -l | xargs echo "  Fleet MC (by prefix):"
oc get managedclusteraddon -A -l "${REPRO_LABEL}" --no-headers 2>/dev/null | wc -l | xargs echo "  ManagedClusterAddOn:"
oc get clusterdeployments.hive.openshift.io -A -l "${REPRO_LABEL}" --no-headers 2>/dev/null | wc -l | xargs echo "  ClusterDeployment:"
oc get policyreport -A -l "${REPRO_LABEL}" --no-headers 2>/dev/null | wc -l | xargs echo "  PolicyReport:"

if [[ "${FORCE}" != true ]]; then
  read -r -p "Continue cleanup? [y/N] " ans
  [[ "${ans}" =~ ^[yY]$ ]] || exit 0
fi

echo "Deleting governance ..."
oc delete -f "${SCRIPT_DIR}/governance/fleet-base.yaml" --ignore-not-found --wait=false 2>/dev/null || true

echo "Deleting layer B/D + fleet CRs ..."
oc delete csr -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete policyreport -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete clusterdeployment -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete machinepool -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete clusterprovision -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete secret -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete clusterimageset -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete managedclusteraddon -A -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true
oc delete managedcluster -l "${REPRO_LABEL}" --wait=false 2>/dev/null || true

while read -r ns; do
  [[ -n "${ns}" ]] || continue
  [[ "${ns}" == "${CLUSTER_PREFIX}-"* ]] || continue
  oc delete namespace "${ns}" --wait=false 2>/dev/null || true
done < <(oc get ns -l "${REPRO_LABEL}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)

if resolve_console_deploy 2>/dev/null; then
  echo "Removing LOG_MEMORY from console deployment (optional) ..."
  oc set env deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" LOG_MEMORY- LOG_LEVEL- 2>/dev/null || true
fi

echo "Cleanup initiated (async). Run ./verify-counts.sh to confirm."
