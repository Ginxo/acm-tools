#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Enable console backend memory logging for repro.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

load_env
require_oc
resolve_console_deploy

oc set env deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
  LOG_MEMORY=true LOG_LEVEL=info

oc rollout status deployment/"${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}"
echo "LOG_MEMORY enabled on ${CONSOLE_DEPLOY}"
