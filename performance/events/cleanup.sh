#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Remove mock fleet and governance resources from the hub.
#
# Why the old cleanup looked like a no-op:
# 1. Mock ManagedClusters have OCM import/cleanup finalizers that never finish
#    (no real spoke) → namespaces stay Terminating forever unless finalizers
#    are stripped.
# 2. Controllers sometimes strip acm-perf-repro labels → label-only deletes miss
#    leftovers; we always also match ${CLUSTER_PREFIX}-*.
# 3. Deleting thousands of ManagedClusterAddOns one-by-one is too slow; wiping
#    the fleet namespaces (after clearing finalizers) cascades them.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

FORCE=false
SOFT=false
WAIT=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=true; shift ;;
    --soft) SOFT=true; shift ;;
    --wait) WAIT=true; shift ;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--force] [--soft] [--wait]

  --force   Skip confirmation prompt
  --soft    Do not strip finalizers (may leave Terminating namespaces)
  --wait    Poll until mock ManagedClusters / namespaces are gone (10m)
EOF
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

count_lines() {
  local n
  n="$(wc -l | tr -d ' ')"
  echo "${n:-0}"
}

# Always prefer prefix listing; label-only misses unlabeled leftovers.
list_all_fleet_mcs() {
  {
    oc get managedcluster -l "${REPRO_LABEL}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true
    oc get managedcluster -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
      | grep -E "^${CLUSTER_PREFIX}-[0-9]+$" || true
  } | sort -u
}

list_all_fleet_namespaces() {
  {
    oc get ns -l "${REPRO_LABEL}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true
    oc get ns -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
      | grep -E "^(${CLUSTER_PREFIX}-[0-9]+|perf-console-cache|${GOVERNANCE_NS})$" || true
  } | sort -u
}

mapfile -t MCS < <(list_all_fleet_mcs)
mapfile -t NSS < <(list_all_fleet_namespaces)

echo "Resources to delete (label ${REPRO_LABEL} / prefix ${CLUSTER_PREFIX}-*):"
echo "  ManagedClusters:          ${#MCS[@]}"
echo "  Fleet/perf namespaces:    ${#NSS[@]}"
echo "  ClusterDeployment (lbl):  $(oc get clusterdeployments.hive.openshift.io -A -l "${REPRO_LABEL}" --no-headers 2>/dev/null | count_lines)"
echo "  PolicyReport (lbl):       $(oc get policyreport -A -l "${REPRO_LABEL}" --no-headers 2>/dev/null | count_lines)"

if [[ "${FORCE}" != true ]]; then
  read -r -p "Continue cleanup? [y/N] " ans
  [[ "${ans}" =~ ^[yY]$ ]] || exit 0
fi

echo "Deleting governance manifests (if present) ..."
oc delete -f "${SCRIPT_DIR}/governance/fleet-base.yaml" --ignore-not-found --wait=false 2>/dev/null || true

echo "Deleting cluster-scoped / leftover labeled CRs (best effort, async) ..."
# Keep this list small and --wait=false. Do NOT delete ManagedClusterAddOn -A here —
# that is O(addons×clusters) and hangs for a long time; namespace wipe cascades them.
for kind in \
  csr \
  clusterimageset \
  clusterrole \
  user.user.openshift.io \
  group.user.openshift.io \
  managedclusterset
do
  oc delete "${kind}" -l "${REPRO_LABEL}" --wait=false --ignore-not-found >/dev/null 2>&1 || true
done

for kind in \
  clusterdeployments.hive.openshift.io \
  machinepool \
  clusterprovision \
  policyreport \
  discoveredcluster \
  hostedcluster \
  nodepool \
  secret \
  managedclusterinfo \
  managedclustersetbinding \
  policy.policy.open-cluster-management.io \
  policyset \
  policyautomation \
  placementbinding \
  placement \
  placementdecision \
  helmrelease \
  subscriptionreport \
  channel \
  subscription.apps.open-cluster-management.io \
  application \
  applications.argoproj.io \
  applicationsets.argoproj.io
do
  oc delete "${kind}" -A -l "${REPRO_LABEL}" --wait=false --ignore-not-found >/dev/null 2>&1 || true
done

if [[ ${#MCS[@]} -gt 0 ]]; then
  echo "Deleting ${#MCS[@]} ManagedClusters ..."
  printf '%s\n' "${MCS[@]}" | xargs -r -n 50 oc delete managedcluster --wait=false --ignore-not-found >/dev/null 2>&1 || true

  if [[ "${SOFT}" != true ]]; then
    echo "Stripping ManagedCluster finalizers (required for mock fleets) ..."
    printf '%s\n' "${MCS[@]}" | xargs -r -n 10 -P 16 bash -c '
      for name in "$@"; do
        oc patch managedcluster "$name" --type=merge \
          -p "{\"metadata\":{\"finalizers\":null}}" >/dev/null 2>&1 || true
      done
    ' _
  fi
fi

if [[ ${#NSS[@]} -gt 0 ]]; then
  echo "Deleting ${#NSS[@]} fleet/perf namespaces (cascades AddOns & namespaced CRs) ..."
  printf '%s\n' "${NSS[@]}" | xargs -r -n 40 oc delete namespace --wait=false --ignore-not-found >/dev/null 2>&1 || true

  if [[ "${SOFT}" != true ]]; then
    echo "Stripping namespace finalizers ..."
    printf '%s\n' "${NSS[@]}" | xargs -r -n 10 -P 16 bash -c '
      for ns in "$@"; do
        oc patch namespace "$ns" --type=merge \
          -p "{\"spec\":{\"finalizers\":null}}" >/dev/null 2>&1 || true
      done
    ' _
  fi
fi

if resolve_console_deploy 2>/dev/null; then
  echo "Removing LOG_MEMORY / LOG_LEVEL from console deployment (optional) ..."
  oc set env deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" LOG_MEMORY- LOG_LEVEL- 2>/dev/null || true
  echo "Note: memory limit from set-console-memory-8gi.sh is NOT auto-reverted."
fi

if [[ "${WAIT}" == true ]]; then
  echo "Waiting for ManagedClusters / ${CLUSTER_PREFIX}-* namespaces to clear (up to 10m) ..."
  deadline=$((SECONDS + 600))
  while (( SECONDS < deadline )); do
    mapfile -t left_mc < <(list_all_fleet_mcs)
    mapfile -t left_ns < <(list_all_fleet_namespaces)
    echo "  remaining: managedclusters=${#left_mc[@]} namespaces=${#left_ns[@]}"
    if [[ ${#left_mc[@]} -eq 0 && ${#left_ns[@]} -eq 0 ]]; then
      echo "Cleanup complete."
      exit 0
    fi
    if [[ "${SOFT}" != true ]]; then
      if [[ ${#left_mc[@]} -gt 0 ]]; then
        printf '%s\n' "${left_mc[@]}" | xargs -r -n 50 oc delete managedcluster --wait=false --ignore-not-found >/dev/null 2>&1 || true
        printf '%s\n' "${left_mc[@]}" | xargs -r -n 10 -P 16 bash -c '
          for name in "$@"; do
            oc patch managedcluster "$name" --type=merge \
              -p "{\"metadata\":{\"finalizers\":null}}" >/dev/null 2>&1 || true
          done
        ' _
      fi
      if [[ ${#left_ns[@]} -gt 0 ]]; then
        printf '%s\n' "${left_ns[@]}" | xargs -r -n 40 oc delete namespace --wait=false --ignore-not-found >/dev/null 2>&1 || true
        printf '%s\n' "${left_ns[@]}" | xargs -r -n 10 -P 16 bash -c '
          for ns in "$@"; do
            oc patch namespace "$ns" --type=merge \
              -p "{\"spec\":{\"finalizers\":null}}" >/dev/null 2>&1 || true
          done
        ' _
      fi
    fi
    sleep 10
  done
  echo "warning: timed out with leftovers — re-run: $0 --force --wait" >&2
  exit 1
fi

echo "Cleanup initiated. Verify with:"
echo "  oc get managedcluster | grep ${CLUSTER_PREFIX}- | wc -l"
echo "  oc get ns | grep ${CLUSTER_PREFIX}- | wc -l"
echo "If objects linger: $(basename "$0") --force --wait"
