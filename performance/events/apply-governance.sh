#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Apply governance scaffolding (ClusterSet, Placement, Policies).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc

echo "Applying governance base from governance/fleet-base.yaml ..."
oc apply -f "${SCRIPT_DIR}/governance/fleet-base.yaml"

echo "Governance resources applied in namespace ${GOVERNANCE_NS}."
echo "After fleet clusters exist, run: ./patch-policy-status.py"
