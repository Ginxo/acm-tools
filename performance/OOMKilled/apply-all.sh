#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/env.sh"

echo "=== pause controllers ==="
bash "$DIR/pause-controllers.sh"

echo "=== layer A ==="
bash "$DIR/apply-layer-a.sh"

echo "=== layer B (ClusterImageSet + MP/CP/secrets) ==="
bash "$DIR/apply-layer-b.sh"

echo "=== layer C (CIM / host inventory) ==="
bash "$DIR/apply-layer-c.sh"

echo "=== layer D (policy pressure) ==="
bash "$DIR/apply-layer-d.sh"

echo "=== gate ==="
bash "$DIR/gate.sh" --retention
