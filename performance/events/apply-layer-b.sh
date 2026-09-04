#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Layer B — Hive density (ClusterDeployment, MachinePool, ClusterProvision, secrets).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

START=1
END=750
BATCH_SIZE="${BATCH_SIZE:-25}"
BATCH_SLEEP="${BATCH_SLEEP:-2}"
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --start N       First cluster index (default: 1)
  --end N         Last cluster index (default: 750)
  --batch-size N  Clusters per pause (default: ${BATCH_SIZE})
  --sleep N       Seconds between batches (default: ${BATCH_SLEEP})
  --dry-run       Print progress only
  --preset NAME   smoke(10) small(50) medium(250) target(750)

Requires fleet namespaces from apply-batch.sh (layer A).
Creates per cluster: 5 secrets, ClusterProvision, 2x MachinePool, ClusterDeployment.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) START="$2"; shift 2 ;;
    --end) END="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --sleep) BATCH_SLEEP="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --preset)
      case "$2" in
        smoke) START=1; END=10 ;;
        small) START=1; END=50 ;;
        medium) START=1; END=250 ;;
        target) START=1; END=750 ;;
        *) echo "unknown preset: $2" >&2; exit 1 ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

apply_clusterimage_set() {
  oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterImageSet
metadata:
  name: ${FLEET_IMAGE_SET}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
    visible: "true"
spec:
  releaseImage: quay.io/openshift-release-dev/ocp-release:4.22.12-x86_64
EOF
}

apply_layer_b_cluster() {
  local name="$1"

  if ! fleet_namespace_exists "${name}"; then
    echo "skip ${name}: namespace missing (run apply-batch.sh first)" >&2
    return 0
  fi

  for secret in \
    "${name}-install-config" \
    "${name}-ssh-key" \
    "${name}-aws-creds" \
    "${name}-admin-kubeconfig" \
    "${name}-admin-password"; do
    oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${secret}
  namespace: ${name}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
    cluster.open-cluster-management.io/credentials: "true"
type: Opaque
data:
  stub: ${FAKE_B64_DATA}
EOF
  done

  oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterProvision
metadata:
  name: ${name}-prov
  namespace: ${name}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
spec:
  clusterDeploymentRef:
    name: ${name}
  podSpec: {}
  attempt: 0
  stage: complete
  infraID: ${name}-infra
  adminKubeconfigSecretRef:
    name: ${name}-admin-kubeconfig
  adminPasswordSecretRef:
    name: ${name}-admin-password
EOF

  for pool in worker infra; do
    oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: MachinePool
metadata:
  name: ${name}-${pool}
  namespace: ${name}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
spec:
  clusterDeploymentRef:
    name: ${name}
  name: ${pool}
  replicas: 1
  platform:
    aws:
      type: m6a.large
      rootVolume:
        size: 120
        type: gp3
EOF
  done

  if oc get clusterdeployment "${name}" -n "${name}" >/dev/null 2>&1; then
    oc patch clusterdeployment "${name}" -n "${name}" --type=merge -p "{
      \"metadata\": {
        \"labels\": {
          \"${REPRO_LABEL_KEY}\": \"${REPRO_LABEL_VALUE}\",
          \"cloud\": \"Amazon\",
          \"vendor\": \"OpenShift\"
        }
      }
    }" >/dev/null 2>&1 || true
  else
    oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterDeployment
metadata:
  name: ${name}
  namespace: ${name}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
    cloud: Amazon
    vendor: OpenShift
spec:
  baseDomain: ${FLEET_BASE_DOMAIN}
  clusterName: ${name}
  installed: true
  preserveOnDelete: true
  clusterMetadata:
    clusterID: ${name}-cluster-id
    infraID: ${name}-infra
    adminKubeconfigSecretRef:
      name: ${name}-admin-kubeconfig
    adminPasswordSecretRef:
      name: ${name}-admin-password
  platform:
    aws:
      credentialsSecretRef:
        name: ${name}-aws-creds
      region: us-east-1
  provisioning:
    imageSetRef:
      name: ${FLEET_IMAGE_SET}
    installConfigSecretRef:
      name: ${name}-install-config
    sshPrivateKeySecretRef:
      name: ${name}-ssh-key
EOF
  fi

  oc patch clusterdeployment "${name}" -n "${name}" --subresource=status --type=merge -p "{
    \"status\": {
      \"powerState\": \"Running\",
      \"provisionRef\": {\"name\": \"${name}-prov\"}
    }
  }" >/dev/null 2>&1 || true

  oc label managedcluster "${name}" "${REPRO_LABEL}" \
    "cluster.open-cluster-management.io/clusterset=${CLUSTER_SET_NAME}" --overwrite >/dev/null 2>&1 || true
}

echo "Layer B (Hive): clusters ${START}-${END}, batch ${BATCH_SIZE}"
if [[ "${DRY_RUN}" == true ]]; then
  echo "[dry-run] would apply ClusterImageSet ${FLEET_IMAGE_SET}"
else
  apply_clusterimage_set
fi

count=0
while read -r name; do
  [[ -n "${name}" ]] || continue
  if [[ "${DRY_RUN}" == true ]]; then
    echo "[dry-run] layer B: ${name}"
  else
    apply_layer_b_cluster "${name}"
    echo "Layer B: ${name}"
  fi
  count=$((count + 1))
  if [[ $((count % BATCH_SIZE)) -eq 0 ]]; then
    sleep "${BATCH_SLEEP}"
  fi
done < <(list_fleet_managedclusters "${START}" "${END}")

echo "Layer B complete (${count} clusters processed)."
"${SCRIPT_DIR}/verify-counts.sh" 2>/dev/null || true
