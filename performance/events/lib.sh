#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Shared helpers for ACM performance / events measurement scripts.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPRO_LABEL_KEY="acm-perf-repro"
REPRO_LABEL_VALUE="true"
REPRO_LABEL="${REPRO_LABEL_KEY}=${REPRO_LABEL_VALUE}"
CLUSTER_SET_NAME="perf-fleet"
GOVERNANCE_NS="perf-governance"
CLUSTER_PREFIX="${CLUSTER_PREFIX:-mock-sno}"
CLUSTER_WIDTH="${CLUSTER_WIDTH:-4}"

CONSOLE_NS="${CONSOLE_NS:-open-cluster-management}"
FLEET_IMAGE_SET="${FLEET_IMAGE_SET:-perf-img-stub}"
FLEET_BASE_DOMAIN="${FLEET_BASE_DOMAIN:-perf.example.com}"
FAKE_B64_DATA="${FAKE_B64_DATA:-e30=}"

load_env() {
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${SCRIPT_DIR}/.env"
    set +a
  fi
}

require_oc() {
  if ! command -v oc >/dev/null 2>&1; then
    echo "error: oc not found in PATH" >&2
    exit 1
  fi
  if ! oc whoami >/dev/null 2>&1; then
    echo "error: not logged in — run: oc login ..." >&2
    exit 1
  fi
}

# Deployments labeled component=console include acm-cli-downloads (static assets, ~128Mi).
# The Node.js backend we need is the other operand (typically much larger memory / SSE /events).
is_excluded_console_deploy() {
  local name="$1"
  [[ "${name}" == *cli-downloads* ]] || [[ "${name}" == *download* ]]
}

memory_limit_mib() {
  local deploy="$1"
  local ns="$2"
  local raw
  raw="$(oc get deploy "${deploy}" -n "${ns}" \
    -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}' 2>/dev/null || true)"
  case "${raw}" in
    ""|"0") echo 0 ;;
    *Gi) echo $(( ${raw%Gi} * 1024 )) ;;
    *Mi) echo "${raw%Mi}" ;;
    *G) echo $(( ${raw%G} * 1000 )) ;;
    *M) echo "${raw%M}" ;;
    *) echo 0 ;;
  esac
}

list_console_deploy_candidates() {
  local ns="$1"
  oc get deploy -n "${ns}" -l component=console \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
    | while read -r name; do
        [[ -n "${name}" ]] || continue
        is_excluded_console_deploy "${name}" && continue
        echo "${name}"
      done
}

find_mce_console_deploy() {
  local name
  # app=console-mce is on the pod template / selector, not deployment metadata labels.
  name="$(oc get deploy -n multicluster-engine \
    -o jsonpath='{range .items[?(@.spec.selector.matchLabels.app=="console-mce")]}{.metadata.name}{"\n"}{end}' \
    2>/dev/null | head -1)"
  if [[ -n "${name}" ]]; then
    echo "${name}"
    return 0
  fi
  if oc get deploy console-mce-console -n multicluster-engine >/dev/null 2>&1; then
    echo "console-mce-console"
    return 0
  fi
  return 1
}

console_events_disabled() {
  local deploy="$1"
  local ns="$2"
  local val
  val="$(oc get deploy "${deploy}" -n "${ns}" \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DISABLE_EVENTS")].value}' 2>/dev/null || true)"
  [[ "${val}" == "true" ]]
}

resolve_console_deploy() {
  if [[ -n "${CONSOLE_DEPLOY:-}" ]]; then
    export CONSOLE_DEPLOY CONSOLE_NS
    return 0
  fi

  local backend="${CONSOLE_BACKEND:-auto}"
  if [[ "${backend}" == "mce" || "${backend}" == "auto" ]]; then
    local mce
    mce="$(find_mce_console_deploy || true)"
    if [[ -n "${mce}" ]]; then
      CONSOLE_NS="multicluster-engine"
      CONSOLE_DEPLOY="${mce}"
      export CONSOLE_DEPLOY CONSOLE_NS
      return 0
    fi
    [[ "${backend}" == "mce" ]] && {
      echo "error: CONSOLE_BACKEND=mce but no console-mce deployment in multicluster-engine" >&2
      exit 1
    }
  fi

  local ns="${CONSOLE_NS}"
  local -a candidates=()
  while IFS= read -r name; do
    [[ -n "${name}" ]] || continue
    console_events_disabled "${name}" "${ns}" && continue
    candidates+=("${name}")
  done < <(list_console_deploy_candidates "${ns}")

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "error: ACM console backend deployment not found in ${ns}" >&2
    echo "Deployments with component=console (including excluded sidecars):" >&2
    oc get deploy -n "${ns}" -l component=console 2>&1 >&2 || true
    echo "Set CONSOLE_DEPLOY=console-mce-console CONSOLE_NS=multicluster-engine in .env if auto-detect fails." >&2
    exit 1
  fi

  local chosen=""
  local name mem best_mem=0
  for name in "${candidates[@]}"; do
    if [[ "${name}" == "console" ]]; then
      chosen="${name}"
      break
    fi
  done

  if [[ -z "${chosen}" ]]; then
    for name in "${candidates[@]}"; do
      mem="$(memory_limit_mib "${name}" "${ns}")"
      if [[ "${mem}" -gt "${best_mem}" ]]; then
        best_mem="${mem}"
        chosen="${name}"
      fi
    done
  fi

  [[ -z "${chosen}" ]] && chosen="${candidates[0]}"
  CONSOLE_DEPLOY="${chosen}"
  export CONSOLE_DEPLOY CONSOLE_NS
}

resolve_console_container_port() {
  local deploy="${1:-${CONSOLE_DEPLOY}}"
  local ns="${2:-${CONSOLE_NS}}"
  local port name

  for name in https tls api server; do
    port="$(oc get deploy "${deploy}" -n "${ns}" \
      -o jsonpath="{.spec.template.spec.containers[0].ports[?(@.name==\"${name}\")].containerPort}" 2>/dev/null || true)"
    if [[ -n "${port}" ]]; then
      echo "${port}"
      return 0
    fi
  done

  port="$(oc get deploy "${deploy}" -n "${ns}" \
    -o jsonpath='{.spec.template.spec.containers[0].ports[*].containerPort}' 2>/dev/null \
    | tr ' ' '\n' | sort -n | tail -1)"
  echo "${port:-9443}"
}

resolve_console_pod() {
  resolve_console_deploy
  local selector
  selector="$(oc get deploy "${CONSOLE_DEPLOY}" -n "${CONSOLE_NS}" \
    -o go-template='{{range $k,$v := .spec.selector.matchLabels}}{{$k}}={{$v}},{{end}}' \
    | sed 's/,$//')"
  CONSOLE_POD="$(oc get pod -n "${CONSOLE_NS}" -l "${selector}" \
    -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' \
    | head -1)"
  export CONSOLE_POD
}

cluster_name() {
  local index="$1"
  printf '%s-%0*d' "${CLUSTER_PREFIX}" "${CLUSTER_WIDTH}" "${index}"
}

# ManagedCluster labels may be stripped by controllers; fall back to name prefix.
list_fleet_managedclusters() {
  local start="${1:-1}"
  local end="${2:-750}"
  local -a names=()
  local name suffix index

  while read -r name; do
    [[ -n "${name}" ]] || continue
    suffix="${name##*-}"
    [[ "${suffix}" =~ ^[0-9]+$ ]] || continue
    index=$((10#${suffix}))
    if [[ "${index}" -ge "${start}" && "${index}" -le "${end}" ]]; then
      if [[ "${name}" == "${CLUSTER_PREFIX}-"* ]]; then
        names+=("${name}")
      fi
    fi
  done < <(oc get managedcluster -l "${REPRO_LABEL}" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)

  if [[ ${#names[@]} -eq 0 ]]; then
    while read -r name; do
      [[ -n "${name}" ]] || continue
      suffix="${name##*-}"
      [[ "${suffix}" =~ ^[0-9]+$ ]] || continue
      index=$((10#${suffix}))
      if [[ "${index}" -ge "${start}" && "${index}" -le "${end}" && "${name}" == "${CLUSTER_PREFIX}-"* ]]; then
        names+=("${name}")
      fi
    done < <(oc get managedcluster -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)
  fi

  printf '%s\n' "${names[@]}" | sort -u
}

fleet_namespace_exists() {
  local name="$1"
  oc get namespace "${name}" >/dev/null 2>&1
}

resolve_console_events_url() {
  local plugin="${CONSOLE_PLUGIN:-auto}"
  local base_url="${CONSOLE_URL:-}"

  if [[ -z "${base_url}" ]]; then
    base_url="$(oc get route -n openshift-console console -o jsonpath='https://{.spec.host}' 2>/dev/null || true)"
    if [[ -z "${base_url}" ]]; then
      base_url="$(oc get routes -A -o jsonpath='{range .items[?(@.metadata.name=="console")]}{.spec.host}{"\n"}{end}' 2>/dev/null | head -1)"
      [[ -n "${base_url}" ]] && base_url="https://${base_url}"
    fi
  fi

  if [[ "${plugin}" == "auto" ]]; then
    if oc get mce multiclusterengine -n multicluster-engine >/dev/null 2>&1; then
      plugin="mce"
    else
      plugin="acm"
    fi
  fi

  CONSOLE_EVENTS_URL="${base_url}/api/proxy/plugin/${plugin}/console/multicloud/events"
  export CONSOLE_EVENTS_URL CONSOLE_PLUGIN="${plugin}"
}
