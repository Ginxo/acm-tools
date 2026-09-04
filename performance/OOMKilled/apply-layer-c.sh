#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

FAKE_PULL='eyJhdXRocyI6e319'
ENV_LABEL_KEY="oom-repro-env"

for i in $(seq -w 1 "$CLUSTERS"); do
  NAME="mock-mc-${i}"
  ENV_LABEL_VAL="${NAME}"

  oc apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: pullsecret-${NAME}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    cluster.open-cluster-management.io/credentials: "true"
type: kubernetes.io/dockerconfigjson
data:
  .dockerconfigjson: ${FAKE_PULL}
EOF

  oc apply -f - <<EOF
apiVersion: extensions.hive.openshift.io/v1beta1
kind: AgentClusterInstall
metadata:
  name: ${NAME}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
spec:
  clusterDeploymentRef:
    name: ${NAME}
  holdInstallation: true
  imageSetRef:
    name: ${IMG}
  networking:
    networkType: OVNKubernetes
    clusterNetwork:
      - cidr: 10.128.0.0/14
        hostPrefix: 23
    serviceNetwork:
      - 172.30.0.0/16
  platformType: BareMetal
  provisionRequirements:
    controlPlaneAgents: 3
    workerAgents: 0
  sshPublicKey: "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC"
EOF

  # Hive treats clusterMetadata as immutable. Installed CDs created without it
  # cannot be patched later — delete + recreate is required.
  ensure_clusterdeployment() {
    local name=$1
    local expected_infra="${name}-infra"
    local existing_infra
    existing_infra="$(oc get clusterdeployment "$name" -n "$name" -o jsonpath='{.spec.clusterMetadata.infraID}' 2>/dev/null || true)"

    if [[ "$existing_infra" == "$expected_infra" ]]; then
      echo "ClusterDeployment ${name} already present with clusterMetadata — skipping"
      return 0
    fi

    if oc get clusterdeployment "$name" -n "$name" &>/dev/null; then
      echo "Recreating ClusterDeployment ${name} (missing/incomplete clusterMetadata)"
      oc delete clusterdeployment "$name" -n "$name" --wait=true --timeout=120s
    fi

    oc create -f - <<EOF
apiVersion: hive.openshift.io/v1
kind: ClusterDeployment
metadata:
  name: ${name}
  namespace: ${name}
  labels:
    ${LABEL}: "true"
    cloud: BareMetal
    vendor: OpenShift
    hive.openshift.io/cluster-platform: agent-baremetal
spec:
  baseDomain: oom.example.com
  clusterName: ${name}
  installed: true
  preserveOnDelete: true
  clusterMetadata:
    clusterID: ${name}-cluster
    infraID: ${name}-infra
    adminKubeconfigSecretRef:
      name: ${name}-admin-kubeconfig
    adminPasswordSecretRef:
      name: ${name}-admin-password
  clusterInstallRef:
    group: extensions.hive.openshift.io
    kind: AgentClusterInstall
    name: ${name}
    version: v1beta1
  platform:
    agentBareMetal:
      agentSelector:
        matchLabels:
          ${ENV_LABEL_KEY}: ${name}
  provisioning:
    imageSetRef:
      name: ${IMG}
    installConfigSecretRef:
      name: ${name}-install-config
    sshPrivateKeySecretRef:
      name: ${name}-ssh-key
  pullSecretRef:
    name: pullsecret-${name}
EOF
  }

  ensure_clusterdeployment "$NAME"

  oc patch clusterdeployment "$NAME" -n "$NAME" --type merge --subresource=status -p "{
    \"status\": {
      \"powerState\": \"Running\",
      \"provisionRef\": {\"name\": \"${NAME}-prov\"}
    }
  }" 2>/dev/null || true

  oc apply -f - <<EOF
apiVersion: agent-install.openshift.io/v1beta1
kind: InfraEnv
metadata:
  name: ${NAME}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    networkType: dhcp
spec:
  clusterRef:
    name: ${NAME}
    namespace: ${NAME}
  agentLabels:
    ${ENV_LABEL_KEY}: ${ENV_LABEL_VAL}
  pullSecretRef:
    name: pullsecret-${NAME}
  sshAuthorizedKey: "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC"
  nmStateConfigLabelSelector:
    matchLabels:
      infraenvs.agent-install.openshift.io: ${NAME}
status:
  agentLabelSelector:
    matchLabels:
      ${ENV_LABEL_KEY}: ${ENV_LABEL_VAL}
  conditions:
    - type: Validated
      status: "True"
      reason: Validated
      message: stub
EOF

  for h in $(seq 1 "$HOSTS_PER_CLUSTER"); do
    HOST_ID=$(printf '%s-host-%02d' "$NAME" "$h")
    MAC=$(printf '00:50:56:%02x:%02x:%02x' $((10#$i)) "$h" $((h * 3)))

    oc apply -f - <<EOF
apiVersion: agent-install.openshift.io/v1beta1
kind: NMStateConfig
metadata:
  name: ${HOST_ID}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    infraenvs.agent-install.openshift.io: ${NAME}
spec:
  config:
    interfaces:
      - name: eth0
        type: ethernet
        state: up
        mac-address: ${MAC}
        ipv4:
          enabled: true
          dhcp: true
  interfaces:
    - name: eth0
      macAddress: ${MAC}
EOF

    oc apply -f - <<EOF
apiVersion: metal3.io/v1alpha1
kind: BareMetalHost
metadata:
  name: ${HOST_ID}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    infraenvs.agent-install.openshift.io: ${NAME}
spec:
  online: true
  bootMACAddress: ${MAC}
  bmc:
    address: redfish://127.0.0.1:443/redfish/v1/Systems/${h}
    credentialsName: ${NAME}-bmc-creds
    disableCertificateVerification: true
status:
  provisioning:
    state: available
  operationalStatus: OK
  poweredOn: true
EOF

    oc apply -f - <<EOF
apiVersion: agent-install.openshift.io/v1beta1
kind: Agent
metadata:
  name: ${HOST_ID}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    ${ENV_LABEL_KEY}: ${ENV_LABEL_VAL}
    infraenvs.agent-install.openshift.io: ${NAME}
spec:
  approved: true
  clusterDeploymentName:
    name: ${NAME}
    namespace: ${NAME}
  hostname: ${HOST_ID}
  role: auto-assign
status:
  debugInfo:
    state: known
    stateInfo: Ready
  role: auto-assign
  inventory:
    hostname: ${HOST_ID}
    cpu:
      count: 16
    memory:
      physicalBytes: 34359738368
    interfaces:
      - name: eth0
        macAddress: ${MAC}
        flags: up
  conditions:
    - type: Connected
      status: "True"
      reason: Connected
EOF

    if oc api-resources 2>/dev/null | rg -q 'agentmachine'; then
      oc apply -f - <<EOF
apiVersion: capi-provider.agent-install.openshift.io/v1alpha1
kind: AgentMachine
metadata:
  name: ${HOST_ID}
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
    cluster.x-k8s.io/cluster-name: ${NAME}
spec:
  agentRef:
    name: ${HOST_ID}
    namespace: ${NAME}
EOF
    fi
  done

  echo "Layer C: ${NAME} (${HOSTS_PER_CLUSTER} hosts)"
done

echo "Layer C complete"
