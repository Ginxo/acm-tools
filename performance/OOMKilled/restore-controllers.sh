#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

REPLICAS_FILE="$WORKDIR/controller-replicas.txt"
[[ -f "$REPLICAS_FILE" ]] || { echo "No $REPLICAS_FILE"; exit 1; }

while read -r ns name rep; do
  [[ -z "$ns" || -z "$name" ]] && continue
  echo "Restoring $ns/$name -> $rep"
  oc -n "$ns" scale "deploy/$name" --replicas="${rep:-1}"
done < "$REPLICAS_FILE"
