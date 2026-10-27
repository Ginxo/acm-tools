#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Layer console-cache — dense resources that match console DefaultWatchSpecs selectors.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env

PRESET="smoke"
CLUSTER_START=1
CLUSTER_END=0
DRY_RUN=false
SKIP_STATUS_PATCH=false
OUT_DIR="${SCRIPT_DIR}/generated"
MANIFEST="${OUT_DIR}/console-cache.yaml"
CLUSTERS_FILE="${OUT_DIR}/console-cache-clusters.txt"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --preset NAME     smoke | target | full (default: smoke)
  --start N         First fleet cluster index (default: 1)
  --end N           Last fleet cluster index (default: smoke=10, target/full=750)
  --dry-run         Generate only
  --skip-status     Skip Policy / PlacementDecision status patches
  -h|--help         This help

Creates Namespace perf-console-cache plus dense objects for every DefaultWatchSpecs
kind that scales (Policies, OCM+Argo apps, Hive extras, discovery/hypershift,
assisted, ansible, submariner, MRA, vm ClusterRoles, Users/Groups, matching
Secrets/CSRs, fat PolicyReports). Skips hub singletons (MCH, Authentication, …).

Requires fleet from apply-batch.sh for realistic Policy.status cluster lists.
Use --preset full for maximum density before ./report-informer-memory.py.
Label: ${REPRO_LABEL} acm-perf-layer=console-cache
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)
      PRESET="$2"
      shift 2
      ;;
    --start) CLUSTER_START="$2"; shift 2 ;;
    --end) CLUSTER_END="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    --skip-status) SKIP_STATUS_PATCH=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

case "${PRESET}" in
  smoke)
    [[ "${CLUSTER_END}" -eq 0 ]] && CLUSTER_END=10
    ;;
  target|full)
    [[ "${CLUSTER_END}" -eq 0 ]] && CLUSTER_END=750
    ;;
  *)
    echo "unknown preset: ${PRESET}" >&2
    exit 1
    ;;
esac

mkdir -p "${OUT_DIR}"

echo "Resolving fleet ManagedClusters (${CLUSTER_START}-${CLUSTER_END}) ..."
: >"${CLUSTERS_FILE}"
if [[ "${DRY_RUN}" != true ]]; then
  require_oc
fi
if oc whoami >/dev/null 2>&1; then
  list_fleet_managedclusters "${CLUSTER_START}" "${CLUSTER_END}" >"${CLUSTERS_FILE}" || true
fi
CLUSTER_N="$(wc -l <"${CLUSTERS_FILE}" | tr -d ' ')"
if [[ "${CLUSTER_N}" -eq 0 ]]; then
  echo "warning: no fleet clusters found — generating synthetic names for Policy.status" >&2
  : >"${CLUSTERS_FILE}"
  i="${CLUSTER_START}"
  while [[ "${i}" -le "${CLUSTER_END}" ]]; do
    cluster_name "${i}" >>"${CLUSTERS_FILE}"
    i=$((i + 1))
  done
  CLUSTER_N="$(wc -l <"${CLUSTERS_FILE}" | tr -d ' ')"
fi
echo "  clusters for status lists: ${CLUSTER_N}"

# Real CSR PEM for apply (placeholder in generator is invalid for apiserver).
CSR_TMP="$(mktemp)"
KEY_TMP="$(mktemp)"
openssl req -new -newkey rsa:2048 -nodes \
  -keyout "${KEY_TMP}" -out "${CSR_TMP}" \
  -subj "/CN=perf-console-cache" >/dev/null 2>&1
CSR_B64="$(base64 -w0 <"${CSR_TMP}" 2>/dev/null || base64 <"${CSR_TMP}" | tr -d '\n')"
rm -f "${KEY_TMP}" "${CSR_TMP}"

echo "Generating console-cache manifest (preset=${PRESET}) ..."
python3 "${SCRIPT_DIR}/generate-console-cache.py" \
  --preset "${PRESET}" \
  --clusters-file "${CLUSTERS_FILE}" \
  --csr-request "${CSR_B64}" \
  >"${MANIFEST}"

echo "Wrote ${MANIFEST} ($(wc -l <"${MANIFEST}" | tr -d ' ') lines)"

if [[ "${DRY_RUN}" == true ]]; then
  echo "[dry-run] skip apply"
  exit 0
fi

echo "Applying console-cache layer ..."
# Server-side apply keeps large objects; ignore CRDs missing on this hub.
set +e
oc apply --server-side --force-conflicts -f "${MANIFEST}" 2>"${OUT_DIR}/console-cache-apply.err"
APPLY_RC=$?
set -e
if [[ "${APPLY_RC}" -ne 0 ]]; then
  echo "warning: oc apply exited ${APPLY_RC} (some CRDs absent or schema mismatch). Summary:" >&2
  if [[ -f "${OUT_DIR}/console-cache-apply.err" ]]; then
    grep -oE 'Kind=[A-Za-z]+' "${OUT_DIR}/console-cache-apply.err" 2>/dev/null \
      | sort | uniq -c | sort -rn | head -20 >&2 || true
    echo "--- sample ---" >&2
    grep -E '^Error from server' "${OUT_DIR}/console-cache-apply.err" 2>/dev/null \
      | sed -E 's#(perf-|mock-)[a-z0-9-]+#\1*#g' | sort -u | head -15 >&2 || true
    echo "Full log: ${OUT_DIR}/console-cache-apply.err" >&2
  fi
fi

if [[ "${SKIP_STATUS_PATCH}" != true ]]; then
  echo "Patching status subresources (Policies via clusters file; others from manifest) ..."
  python3 "${SCRIPT_DIR}/patch-console-cache-status.py" "${MANIFEST}" \
    --clusters-file "${CLUSTERS_FILE}" \
    || echo "warning: status patch helper failed" >&2
fi

echo "Layer console-cache apply finished (preset=${PRESET})."
echo "Next: ./report-informer-memory.py  OR  ./gate-density.sh ${CLUSTER_START} ${CLUSTER_END}"
"${SCRIPT_DIR}/verify-counts.sh" 2>/dev/null || true
