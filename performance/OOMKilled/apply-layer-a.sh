#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

for i in $(seq -w 1 "$CLUSTERS"); do
  NAME="acm39327-mc-${i}"
  oc apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${NAME}
  labels:
    ${LABEL}: "true"
---
apiVersion: cluster.open-cluster-management.io/v1
kind: ManagedCluster
metadata:
  name: ${NAME}
  annotations:
    open-cluster-management/created-via: hive
  labels:
    vendor: OpenShift
    cloud: BareMetal
    name: ${NAME}
    ${LABEL}: "true"
    cluster.open-cluster-management.io/clusterset: global
    feature.open-cluster-management.io/addon-application-manager: available
    feature.open-cluster-management.io/addon-cert-policy-controller: available
    feature.open-cluster-management.io/addon-cluster-proxy: available
    feature.open-cluster-management.io/addon-config-policy-controller: available
    feature.open-cluster-management.io/addon-governance-policy-framework: available
    feature.open-cluster-management.io/addon-hypershift-addon: available
    feature.open-cluster-management.io/addon-managed-serviceaccount: available
    feature.open-cluster-management.io/addon-work-manager: available
spec:
  hubAcceptsClient: true
---
apiVersion: internal.open-cluster-management.io/v1beta1
kind: ManagedClusterInfo
metadata:
  name: ${NAME}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    vendor: OpenShift
    cloud: BareMetal
    name: ${NAME}
spec:
  masterEndpoint: https://api.${NAME}.acm39327.example.com:6443
status:
  cloudVendor: BareMetal
  consoleURL: https://console-openshift-console.apps.${NAME}.acm39327.example.com
  kubeVendor: OpenShift
  distributionInfo:
    type: OCP
    ocp:
      version: "4.16.17"
      desiredVersion: "4.16.17"
  nodeList:
    - name: ${NAME}-master-0
      conditions: [{type: Ready, status: "True"}]
      capacity: {cpu: "8", memory: "32Gi"}
    - name: ${NAME}-master-1
      conditions: [{type: Ready, status: "True"}]
      capacity: {cpu: "8", memory: "32Gi"}
    - name: ${NAME}-master-2
      conditions: [{type: Ready, status: "True"}]
      capacity: {cpu: "8", memory: "32Gi"}
EOF

  oc patch managedcluster "$NAME" --type merge --subresource=status -p '{
    "status": {
      "conditions": [
        {"type":"HubAcceptedManagedCluster","status":"True","reason":"HubClusterAdminAccepted","message":"Accepted by hub"},
        {"type":"ManagedClusterJoined","status":"True","reason":"ManagedClusterJoined","message":"Joined"},
        {"type":"ManagedClusterConditionAvailable","status":"True","reason":"ManagedClusterAvailable","message":"Available"},
        {"type":"ManagedClusterImportSucceeded","status":"True","reason":"ManagedClusterImported","message":"Import succeeded"}
      ],
      "version": {"kubernetes": "v1.29.8"}
    }
  }' 2>/dev/null || true

  for addon in "${ADDONS[@]}"; do
    oc apply -f - <<EOF
apiVersion: addon.open-cluster-management.io/v1alpha1
kind: ManagedClusterAddOn
metadata:
  name: ${addon}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
spec:
  installNamespace: open-cluster-management-agent-addon
EOF
  done

  echo "Layer A: ${NAME}"
done

oc create ns open-cluster-management-global-set --dry-run=client -o yaml | oc apply -f -
for p in 1 2 3; do
  oc apply -f - <<EOF
apiVersion: policy.open-cluster-management.io/v1
kind: Policy
metadata:
  name: acm39327-policy-${p}
  namespace: open-cluster-management-global-set
  labels:
    ${LABEL}: "true"
spec:
  disabled: true
  remediationAction: inform
  policy-templates: []
EOF
done

echo "Layer A complete"
