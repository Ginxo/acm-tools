#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Apply console-cache layer (if needed) and generate informer memory report.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

PRESET="${PRESET:-target}"
CLUSTER_START="${CLUSTER_START:-1}"
CLUSTER_END="${CLUSTER_END:-0}"
SKIP_APPLY="${SKIP_APPLY:-false}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [--preset smoke|target] [--skip-apply] [--start N] [--end N]

  1. Optionally apply ./apply-layer-console.sh
  2. Run ./report-informer-memory.py
  3. Print top consumers from generated/informer-memory.md

Environment:
  PRESET, SKIP_APPLY, CLUSTER_START, CLUSTER_END
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset) PRESET="$2"; shift 2 ;;
    --skip-apply) SKIP_APPLY=true; shift ;;
    --start) CLUSTER_START="$2"; shift 2 ;;
    --end) CLUSTER_END="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

load_env
require_oc

if [[ "${SKIP_APPLY}" != true ]]; then
  echo "=== Applying console-cache (preset=${PRESET}) ==="
  args=(--preset "${PRESET}" --start "${CLUSTER_START}")
  [[ "${CLUSTER_END}" -gt 0 ]] && args+=(--end "${CLUSTER_END}")
  "${SCRIPT_DIR}/apply-layer-console.sh" "${args[@]}"
else
  echo "=== Skipping apply (SKIP_APPLY=true) ==="
fi

echo "=== Informer memory report ==="
python3 "${SCRIPT_DIR}/report-informer-memory.py"

echo
echo "Full report: ${SCRIPT_DIR}/generated/informer-memory.md"
echo "CSV:         ${SCRIPT_DIR}/generated/informer-memory.csv"
