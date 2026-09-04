#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

REPLICAS_FILE="$WORKDIR/controller-replicas.txt"
: > "$REPLICAS_FILE"

record_and_scale() {
  local ns=$1 pattern=$2
  while read -r name rep; do
    [[ -z "$name" ]] && continue
    echo "$ns $name $rep" >> "$REPLICAS_FILE"
    echo "Scaling $ns/$name $rep -> 0"
    oc -n "$ns" scale "deploy/$name" --replicas=0
  done < <(
    oc -n "$ns" get deploy -o custom-columns=NAME:.metadata.name,REP:.spec.replicas --no-headers 2>/dev/null \
      | rg -i "$pattern" || true
  )
}

record_and_scale multicluster-engine 'addon-manager|clustermanagementaddon|managed-serviceaccount|assisted-service|agent-controller|infrastructure-operator'
record_and_scale hive 'hive-controllers|clustersync' || true

echo "Saved replicas to $REPLICAS_FILE"
