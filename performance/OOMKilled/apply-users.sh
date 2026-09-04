#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

HTPASSWD_FILE="$WORKDIR/users.htpasswd"
OAUTH_NS=openshift-authentication
SKIP_CONFIRM=false
REGENERATE=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create/update ACM-39327 repro HTPasswd IDP (${ACM_IDP_NAME}) with ${ACM_NONE_USERS_COUNT}
none users (acm-none-01..${ACM_NONE_USERS_COUNT} / ${ACM_NONE_PASS}).

Options:
  --regenerate     Rebuild users.htpasswd even if it already exists
  --skip-confirm   Do not prompt for manual smoke-login confirmation
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --regenerate) REGENERATE=true ;;
    --skip-confirm) SKIP_CONFIRM=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

ensure_htpasswd() {
  if [[ -f "$HTPASSWD_FILE" ]] && [[ "$(wc -l <"$HTPASSWD_FILE")" -ge "$ACM_NONE_USERS_COUNT" ]] && ! $REGENERATE; then
    echo "Using existing $HTPASSWD_FILE"
    return
  fi

  need_cmd htpasswd
  echo "Generating $HTPASSWD_FILE (${ACM_NONE_USERS_COUNT} users)..."
  rm -f "$HTPASSWD_FILE"
  local first=true
  for i in $(seq -w 1 "$ACM_NONE_USERS_COUNT"); do
    if $first; then
      htpasswd -cbB "$HTPASSWD_FILE" "acm-none-${i}" "$ACM_NONE_PASS" >/dev/null
      first=false
    else
      htpasswd -bB "$HTPASSWD_FILE" "acm-none-${i}" "$ACM_NONE_PASS" >/dev/null
    fi
  done
}

apply_secret() {
  echo "Applying secret ${ACM_HTPASSWD_SECRET} in openshift-config..."
  oc create secret generic "$ACM_HTPASSWD_SECRET" \
    --from-file=htpasswd="$HTPASSWD_FILE" \
    -n openshift-config \
    --dry-run=client -o yaml | oc apply -f -
}

has_idp() {
  oc get oauth cluster -o json \
    | jq -e --arg n "$ACM_IDP_NAME" '.spec.identityProviders[]? | select(.name == $n)' >/dev/null
}

apply_oauth_idp() {
  if has_idp; then
    echo "OAuth identity provider ${ACM_IDP_NAME} already present — updating secret only"
    return
  fi

  echo "Adding OAuth identity provider ${ACM_IDP_NAME}..."
  local oauth_json idp_json patch
  oauth_json="$(oc get oauth cluster -o json)"
  idp_json="$(jq -n \
    --arg name "$ACM_IDP_NAME" \
    --arg secret "$ACM_HTPASSWD_SECRET" \
    '{
      name: $name,
      mappingMethod: "claim",
      type: "HTPasswd",
      htpasswd: { fileData: { name: $secret } }
    }')"

  if jq -e '.spec.identityProviders' <<<"$oauth_json" >/dev/null; then
    patch="$(jq --argjson idp "$idp_json" \
      '.spec.identityProviders += [$idp]' <<<"$oauth_json" \
      | jq '{spec: {identityProviders: .spec.identityProviders}}')"
    oc patch oauth cluster --type merge -p "$patch"
  else
    patch="$(jq --argjson idp "$idp_json" \
      '.spec.identityProviders = [$idp]' <<<"$oauth_json" \
      | jq '{spec: {identityProviders: .spec.identityProviders}}')"
    oc patch oauth cluster --type merge -p "$patch"
  fi
}

wait_for_oauth() {
  echo "Waiting for oauth-openshift rollout..."
  oc -n "$OAUTH_NS" rollout status deployment/oauth-openshift --timeout=300s
}

verify_no_clusterrolebindings() {
  echo "Checking for ClusterRoleBindings on acm-none-* users..."
  local hits=0 binding user
  while read -r binding user; do
    [[ -z "$binding" ]] && continue
    echo "  FOUND: $binding -> $user"
    hits=$((hits + 1))
  done < <(
    oc get clusterrolebinding -o json \
      | jq -r --argjson n "$ACM_NONE_USERS_COUNT" '
          .items[]
          | .metadata.name as $b
          | .subjects[]?
          | select(.kind == "User" and (.name | test("^acm-none-[0-9]+$")))
          | select((.name | capture("acm-none-(?<i>[0-9]+)$").i | tonumber) <= $n)
          | "\($b) \(.name)"'
  )

  if [[ "$hits" -gt 0 ]]; then
    echo "FAIL: ${hits} ClusterRoleBinding(s) found for repro users (expected 0)" >&2
    exit 1
  fi
  echo "OK: no ClusterRoleBindings for acm-none-01..${ACM_NONE_USERS_COUNT}"
}

verify_idp() {
  has_idp || { echo "FAIL: OAuth IDP ${ACM_IDP_NAME} not found after apply" >&2; exit 1; }
  oc -n openshift-config get secret "$ACM_HTPASSWD_SECRET" >/dev/null
  local lines
  lines="$(wc -l <"$HTPASSWD_FILE")"
  [[ "$lines" -ge "$ACM_NONE_USERS_COUNT" ]] || {
    echo "FAIL: $HTPASSWD_FILE has $lines lines, expected >= ${ACM_NONE_USERS_COUNT}" >&2
    exit 1
  }
  echo "OK: IDP configured, secret present, ${lines} htpasswd entries"
}

print_smoke_login() {
  local console_url
  console_url="$(oc get route -A -o json 2>/dev/null \
    | jq -r '.items[] | select(.metadata.name|test("console")) | "https://\(.spec.host)"' \
    | head -1)"
  [[ -n "$console_url" && "$console_url" != "null" ]] || console_url="<set CONSOLE_URL manually>"

  cat <<EOF

=== Smoke login (once, before memory measurement) ===
  URL:      ${console_url}
  IDP:      ${ACM_IDP_NAME}
  User:     acm-none-01
  Password: ${ACM_NONE_PASS}

Playwright measured run exports:
  export CONSOLE_URL='${console_url}'
  export ACM_NONE_PASS='${ACM_NONE_PASS}'
  export ACM_IDP_NAME='${ACM_IDP_NAME}'
  export ACM_NONE_USERS="\$(seq -w 1 ${ACM_NONE_USERS_COUNT} | tr '\\n' ' ')"
EOF
}

confirm_smoke_login() {
  print_smoke_login
  if $SKIP_CONFIRM; then
    echo
    echo "Skipping smoke-login confirmation (--skip-confirm)."
    return
  fi
  echo
  read -r -p "Log in as acm-none-01 in the browser, then press Enter to confirm (Ctrl+C to abort)... "
  echo "Smoke-login confirmed."
}

main() {
  need_cmd oc
  need_cmd jq
  oc whoami >/dev/null 2>&1 || {
    echo "Not logged in to the hub. Run: oc login ..." >&2
    exit 1
  }

  ensure_htpasswd
  apply_secret
  apply_oauth_idp
  wait_for_oauth
  verify_idp
  verify_no_clusterrolebindings
  confirm_smoke_login
  echo "Users setup complete."
}

main "$@"
