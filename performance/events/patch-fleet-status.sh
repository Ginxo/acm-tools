#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Patch ManagedCluster / ManagedClusterInfo status and labels for mock fleet clusters.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

START=1
END=10

usage() {
  echo "Usage: $(basename "$0") --start N --end N"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) START="$2"; shift 2 ;;
    --end) END="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

patch_one() {
  local name="$1"
  oc patch managedcluster "${name}" --subresource=status --type=merge -p '{
    "status": {
      "conditions": [
        {"type":"HubAcceptedManagedCluster","status":"True","reason":"HubClusterAdminAccepted","message":"mock fleet"},
        {"type":"ManagedClusterJoined","status":"True","reason":"ManagedClusterJoined","message":"mock fleet"},
        {"type":"ManagedClusterConditionAvailable","status":"True","reason":"ManagedClusterAvailable","message":"mock fleet"},
        {"type":"ManagedClusterImportSucceeded","status":"True","reason":"ManagedClusterImported","message":"mock fleet"},
        {"type":"ManagedClusterConditionClockSynced","status":"True","reason":"ManagedClusterClockSynced","message":"mock fleet"}
      ],
      "version": {"kubernetes": "v1.29.0"}
    }
  }' >/dev/null 2>&1 || true

  oc label managedcluster "${name}" \
    "${REPRO_LABEL_KEY}=${REPRO_LABEL_VALUE}" \
    "cluster.open-cluster-management.io/clusterset=${CLUSTER_SET_NAME}" \
    --overwrite >/dev/null 2>&1 || true

  if oc get managedclusterinfo "${name}" -n "${name}" >/dev/null 2>&1; then
    oc label managedclusterinfo "${name}" -n "${name}" \
      "${REPRO_LABEL_KEY}=${REPRO_LABEL_VALUE}" \
      "cluster.open-cluster-management.io/clusterset=${CLUSTER_SET_NAME}" \
      --overwrite >/dev/null 2>&1 || true

    oc patch managedclusterinfo "${name}" -n "${name}" --subresource=status --type=merge -p "{
      \"status\": {
        \"cloudVendor\": \"Amazon\",
        \"kubeVendor\": \"OpenShift\",
        \"distributionInfo\": {
          \"type\": \"OCP\",
          \"ocp\": {
            \"version\": \"4.22.12\",
            \"desiredVersion\": \"4.22.12\",
            \"channel\": \"stable-4.22\"
          }
        },
        \"conditions\": [
          {\"type\":\"ManagedClusterInfoSynced\",\"status\":\"True\",\"reason\":\"ManagedClusterInfoSynced\",\"message\":\"mock fleet\"}
        ],
        \"nodeList\": [
          {
            \"name\": \"${name}-sno\",
            \"capacity\": {\"cpu\": \"8\", \"memory\": \"32159900Ki\", \"socket\": \"1\"},
            \"conditions\": [{\"type\": \"Ready\", \"status\": \"True\"}],
            \"labels\": {
              \"node-role.kubernetes.io/control-plane\": \"\",
              \"node-role.kubernetes.io/master\": \"\",
              \"node-role.kubernetes.io/worker\": \"\"
            }
          }
        ]
      }
    }" >/dev/null 2>&1 || true
  fi
}

echo "Patching status/labels for clusters ${START}-${END} ..."
for index in $(seq "${START}" "${END}"); do
  patch_one "$(cluster_name "${index}")"
done

echo "Status patch pass complete."
