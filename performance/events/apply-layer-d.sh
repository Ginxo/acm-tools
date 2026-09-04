#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Layer D — PolicyReport, CSR, and policy status (governance SSE pressure).

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
SKIP_CSR=false
SKIP_POLICY_STATUS=false
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --start N              First cluster index (default: 1)
  --end N                Last cluster index (default: 750)
  --batch-size N         Pause every N clusters (default: ${BATCH_SIZE})
  --skip-csr             Skip CertificateSigningRequest creation (faster)
  --skip-policy-status   Skip patch-policy-status.py at end
  --dry-run              Print only
  --preset NAME          smoke|small|medium|target

Creates per cluster: PolicyReport (+ optional CSR).
Runs patch-policy-status.py unless --skip-policy-status.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) START="$2"; shift 2 ;;
    --end) END="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --sleep) BATCH_SLEEP="$2"; shift 2 ;;
    --skip-csr) SKIP_CSR=true; shift ;;
    --skip-policy-status) SKIP_POLICY_STATUS=true; shift ;;
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

apply_policy_report() {
  local name="$1"
  oc apply -f - <<EOF
apiVersion: wgpolicyk8s.io/v1alpha2
kind: PolicyReport
metadata:
  name: ${name}-policyreport
  namespace: ${name}
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
results:
  - policy: perf-policy1
    rule: namespace-must-exist
    result: fail
    severity: medium
    message: mock policy report (policy1)
  - policy: perf-policy2
    rule: limitrange-must-exist
    result: pass
    severity: low
    message: mock policy report (policy2)
  - policy: perf-policy3
    rule: networkpolicy-must-exist
    result: warn
    severity: high
    message: mock policy report (policy3)
EOF
}

apply_csr() {
  local name="$1"
  local key csrfile req
  key="$(mktemp)"
  csrfile="$(mktemp)"
  openssl req -new -newkey rsa:2048 -nodes \
    -keyout "${key}" -out "${csrfile}" \
    -subj "/CN=${name}" >/dev/null 2>&1
  req="$(base64 -w0 <"${csrfile}" 2>/dev/null || base64 <"${csrfile}" | tr -d '\n')"
  rm -f "${key}" "${csrfile}"

  oc apply -f - <<EOF
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: ${name}-csr
  labels:
    ${REPRO_LABEL_KEY}: "${REPRO_LABEL_VALUE}"
    open-cluster-management.io/cluster-name: ${name}
spec:
  request: ${req}
  signerName: kubernetes.io/kube-apiserver-client
  usages:
    - client auth
EOF
}

echo "Layer D (governance pressure): clusters ${START}-${END}"
count=0
while read -r name; do
  [[ -n "${name}" ]] || continue
  if ! fleet_namespace_exists "${name}"; then
    echo "skip ${name}: namespace missing" >&2
    continue
  fi

  if [[ "${DRY_RUN}" == true ]]; then
    echo "[dry-run] layer D: ${name}"
  else
    apply_policy_report "${name}"
    if [[ "${SKIP_CSR}" != true ]]; then
      apply_csr "${name}" 2>/dev/null || echo "warning: CSR failed for ${name}" >&2
    fi
    echo "Layer D: ${name}"
  fi

  count=$((count + 1))
  if [[ $((count % BATCH_SIZE)) -eq 0 ]]; then
    sleep "${BATCH_SLEEP}"
  fi
done < <(list_fleet_managedclusters "${START}" "${END}")

if [[ "${SKIP_POLICY_STATUS}" != true && "${DRY_RUN}" != true ]]; then
  echo "Patching policy status (${START}-${END}) ..."
  python3 "${SCRIPT_DIR}/patch-policy-status.py" --start "${START}" --end "${END}" \
    --prefix "${CLUSTER_PREFIX}" --width "${CLUSTER_WIDTH}"
fi

echo "Layer D complete (${count} clusters processed)."
"${SCRIPT_DIR}/verify-counts.sh" 2>/dev/null || true
