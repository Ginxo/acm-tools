#!/usr/bin/env bash
# Source before any apply/gate script: source ./env.sh
_OOM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WORKDIR="${WORKDIR:-${_OOM_DIR}}"
export LABEL=acm39327-repro
export CLUSTERS=25
export HOSTS_PER_CLUSTER=10
export MCE_NS=multicluster-engine
export IMG=acm39327-img-stub
export ACM_IDP_NAME=acm39327-htpasswd
export ACM_HTPASSWD_SECRET=acm39327-htpasswd-secret
export ACM_NONE_PASS='Acm39327!'
export ACM_NONE_USERS_COUNT=20
export ADDONS=(
  application-manager
  cert-policy-controller
  cluster-proxy
  config-policy-controller
  governance-policy-framework
  hypershift-addon
  managed-serviceaccount
  work-manager
)
