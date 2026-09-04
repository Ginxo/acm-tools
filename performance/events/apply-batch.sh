#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Apply mock fleet in batches with optional status patch.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

START=1
END=10
DRY_RUN=false
PATCH_STATUS=true
RESOURCE=fleet
GENERATED_DIR="${SCRIPT_DIR}/generated"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

  --start N          First cluster index (default: 1)
  --end N            Last cluster index (required if not using presets)
  --batch-size N     Clusters per oc apply (default: ${BATCH_SIZE:-10})
  --sleep N          Seconds between batches (default: ${BATCH_SLEEP:-2})
  --resource TYPE    fleet (default)|all|managedcluster|managedclusterinfo|addons
  --no-patch-status  Skip status patch after apply
  --dry-run          Print commands only
  --preset STAGE     Shorthand: smoke(10), small(50), medium(250), target(750), stretch(1500)

Examples:
  $(basename "$0") --preset smoke
  $(basename "$0") --start 11 --end 50
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) START="$2"; shift 2 ;;
    --end) END="$2"; shift 2 ;;
    --batch-size) BATCH_SIZE="$2"; shift 2 ;;
    --sleep) BATCH_SLEEP="$2"; shift 2 ;;
    --resource) RESOURCE="$2"; shift 2 ;;
    --no-patch-status) PATCH_STATUS=false; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --preset)
      case "$2" in
        smoke) START=1; END=10 ;;
        small) START=1; END=50 ;;
        medium) START=1; END=250 ;;
        target) START=1; END=750 ;;
        stretch) START=1; END=1500 ;;
        *) echo "unknown preset: $2" >&2; exit 1 ;;
      esac
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

BATCH_SIZE="${BATCH_SIZE:-10}"
BATCH_SLEEP="${BATCH_SLEEP:-2}"

mkdir -p "${GENERATED_DIR}"

echo "Applying mock fleet: ${CLUSTER_PREFIX} indices ${START}-${END} (batch ${BATCH_SIZE})"

batch_start="${START}"
while [[ "${batch_start}" -le "${END}" ]]; do
  batch_end=$((batch_start + BATCH_SIZE - 1))
  if [[ "${batch_end}" -gt "${END}" ]]; then
    batch_end="${END}"
  fi

  manifest="${GENERATED_DIR}/fleet-${batch_start}-${batch_end}.yaml"
  echo "Generating ${manifest} ..."
  python3 "${SCRIPT_DIR}/generate-mock-fleet.py" \
    --start "${batch_start}" --end "${batch_end}" \
    --prefix "${CLUSTER_PREFIX}" --width "${CLUSTER_WIDTH}" \
    --resource "${RESOURCE}" > "${manifest}"

  if [[ "${DRY_RUN}" == true ]]; then
    echo "[dry-run] oc apply -f ${manifest}"
  else
    echo "Applying batch ${batch_start}-${batch_end} ..."
    oc apply -f "${manifest}"
    # OCM creates ManagedClusterInfo when ManagedCluster appears; give controllers a moment.
    sleep 2
    if [[ "${PATCH_STATUS}" == true && "${RESOURCE}" == "fleet" || "${RESOURCE}" == "all" ]]; then
      "${SCRIPT_DIR}/patch-fleet-status.sh" --start "${batch_start}" --end "${batch_end}"
    fi
  fi

  batch_start=$((batch_end + 1))
  if [[ "${batch_start}" -le "${END}" && "${DRY_RUN}" == false ]]; then
    sleep "${BATCH_SLEEP}"
  fi
done

echo "Done. Verify:"
"${SCRIPT_DIR}/verify-counts.sh"
