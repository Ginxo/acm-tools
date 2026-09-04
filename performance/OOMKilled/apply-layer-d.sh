#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

NONCOMPLIANT_1=(3 18 23)
NONCOMPLIANT_2=(1 33 43)
NONCOMPLIANT_3=(5 33 69)

compliant_for() {
  local pinx=$1 inx=$2
  local -n arr="NONCOMPLIANT_${pinx}"
  for v in "${arr[@]}"; do
    [[ "$v" -eq "$inx" ]] && { echo NonCompliant; return; }
  done
  echo Compliant
}

for i in $(seq -w 1 "$CLUSTERS"); do
  NAME="mock-mc-${i}"
  inx=$((10#$i))

  oc apply -f - <<EOF
apiVersion: wgpolicyk8s.io/v1alpha2
kind: PolicyReport
metadata:
  name: ${NAME}-report
  namespace: ${NAME}
  labels:
    ${LABEL}: "true"
results:
  - policy: oom-policy-1
    rule: stub-rule
    result: fail
    severity: medium
    message: repro stub policy report
  - policy: oom-policy-2
    rule: stub-rule-2
    result: pass
    severity: low
    message: repro stub policy report 2
EOF

  if oc api-resources --api-group=certificates.k8s.io 2>/dev/null | rg -q '^certificatesigningrequests[[:space:]]'; then
    # Kubernetes rejects non-PEM CSR payloads; generate a real request once per cluster.
    KEY=$(mktemp)
    CSRFILE=$(mktemp)
    openssl req -new -newkey rsa:2048 -nodes \
      -keyout "$KEY" -out "$CSRFILE" \
      -subj "/CN=${NAME}" >/dev/null 2>&1
    REQ=$(base64 -w0 <"$CSRFILE")
    rm -f "$KEY" "$CSRFILE"
    oc apply -f - <<EOF
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: ${NAME}-csr
  labels:
    ${LABEL}: "true"
    open-cluster-management.io/cluster-name: ${NAME}
spec:
  request: ${REQ}
  signerName: kubernetes.io/kube-apiserver-client
  usages:
    - client auth
EOF
  fi
done

for p in 1 2 3; do
  STATUS_JSON='['
  for i in $(seq -w 1 "$CLUSTERS"); do
    NAME="mock-mc-${i}"
    inx=$((10#$i))
    compliant=$(compliant_for "$p" "$inx")
    STATUS_JSON+="{\"clustername\":\"${NAME}\",\"clusternamespace\":\"${NAME}\",\"compliant\":\"${compliant}\"},"
  done
  STATUS_JSON="${STATUS_JSON%,}]"

  oc patch policy "oom-policy-${p}" -n open-cluster-management-global-set --type merge --subresource=status -p "{\"status\":{\"status\":${STATUS_JSON},\"compliant\":\"NonCompliant\"}}" 2>/dev/null || true
done

echo "Layer D complete"
