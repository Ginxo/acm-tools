#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

FAKE_PULL='eyJhdXRocyI6e319'
FAKE_DATA='e30='

oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterImageSet
metadata:
  name: ${IMG}
  labels:
    ${LABEL}: "true"
    visible: "true"
spec:
  releaseImage: quay.io/openshift-release-dev/ocp-release:4.16.17-x86_64
EOF

for i in $(seq -w 1 "$CLUSTERS"); do
  NAME="acm39327-mc-${i}"

  for secret in "${NAME}-install-config" "${NAME}-ssh-key" "${NAME}-bmc-creds" "${NAME}-admin-kubeconfig" "${NAME}-admin-password"; do
    oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${secret}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    cluster.open-cluster-management.io/credentials: "true"
type: Opaque
data:
  stub: ${FAKE_DATA}
EOF
  done

  oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterProvision
metadata:
  name: ${NAME}-prov
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
spec:
  clusterDeploymentRef:
    name: ${NAME}
  podSpec: {}
  attempt: 0
  stage: complete
  infraID: ${NAME}-infra
  adminKubeconfigSecretRef:
    name: ${NAME}-admin-kubeconfig
  adminPasswordSecretRef:
    name: ${NAME}-admin-password
EOF

  for pool in worker infra; do
    oc apply -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: MachinePool
metadata:
  name: ${NAME}-${pool}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
spec:
  clusterDeploymentRef:
    name: ${NAME}
  name: ${pool}
  replicas: 3
  platform:
    aws:
      type: m6a.2xlarge
      rootVolume:
        size: 120
        type: gp3
EOF
  done

  echo "Layer B: ${NAME}"
done

echo "Layer B complete"
