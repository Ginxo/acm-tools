#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Repeat measure-sse runs and save JSON for before/after performance comparison.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]
       $(basename "$0") --compare BASELINE.json AFTER.json

Record one or more SSE measurements (same hub + backend state) into JSON.

Environment:
  BENCHMARK_LABEL     Name for this run (default: run)
  BENCHMARK_RUNS      Repeat count (default: 3)
  BENCHMARK_WARMUP    If true, discard one warmup run before recording (default: false)
  BENCHMARK_DIR       Output directory (default: generated/benchmarks)
  BENCHMARK_NOTES     Free-text note stored in JSON (e.g. git branch, JIRA)
  CONSOLE_REPO        Path to console clone for git rev in metadata (optional)
  SSE_MODE            local | direct | proxy (default: local)
  SSE_MAX_SECONDS     Per-run timeout (default: 600)

Examples:
  # Baseline (local dev backend must already be running)
  BENCHMARK_LABEL=baseline BENCHMARK_RUNS=3 SSE_MODE=local ./benchmark-sse.sh

  # After your code change — restart backend first for fair cold-ish comparison
  BENCHMARK_LABEL=after-ssar-cache BENCHMARK_RUNS=3 SSE_MODE=local ./benchmark-sse.sh

  # Compare
  ./benchmark-sse.sh --compare generated/benchmarks/baseline-*.json generated/benchmarks/after-*.json
EOF
}

load_env
require_oc

BENCHMARK_LABEL="${BENCHMARK_LABEL:-run}"
BENCHMARK_RUNS="${BENCHMARK_RUNS:-3}"
BENCHMARK_WARMUP="${BENCHMARK_WARMUP:-false}"
BENCHMARK_DIR="${BENCHMARK_DIR:-${SCRIPT_DIR}/generated/benchmarks}"
BENCHMARK_NOTES="${BENCHMARK_NOTES:-}"
CONSOLE_REPO="${CONSOLE_REPO:-}"
SSE_MODE="${SSE_MODE:-local}"
MAX_SECONDS="${SSE_MAX_SECONDS:-600}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--compare" ]]; then
  [[ $# -eq 3 ]] || {
    usage >&2
    exit 1
  }
  exec python3 "${SCRIPT_DIR}/compare-sse-benchmarks.py" "$2" "$3"
fi

mkdir -p "${BENCHMARK_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BENCHMARK_DIR}/${BENCHMARK_LABEL}-${STAMP}.json"
TMP_RUNS="$(mktemp)"

cleanup() {
  rm -f "${TMP_RUNS}"
}
trap cleanup EXIT

console_git=""
if [[ -n "${CONSOLE_REPO}" && -d "${CONSOLE_REPO}/.git" ]]; then
  console_git="$(git -C "${CONSOLE_REPO}" rev-parse --short HEAD 2>/dev/null || true)"
fi

echo "Benchmark label: ${BENCHMARK_LABEL}" >&2
echo "Mode: ${SSE_MODE}  runs: ${BENCHMARK_RUNS}  timeout: ${MAX_SECONDS}s" >&2
echo "Output: ${OUT}" >&2

if [[ "${BENCHMARK_WARMUP}" == true ]]; then
  echo "Warmup run (discarded) ..." >&2
  SSE_MODE="${SSE_MODE}" SSE_MAX_SECONDS="${MAX_SECONDS}" \
    "${SCRIPT_DIR}/measure-sse.sh" --quiet --json >/dev/null
fi

for i in $(seq 1 "${BENCHMARK_RUNS}"); do
  echo "Run ${i}/${BENCHMARK_RUNS} ..." >&2
  if ! SSE_MODE="${SSE_MODE}" SSE_MAX_SECONDS="${MAX_SECONDS}" \
    "${SCRIPT_DIR}/measure-sse.sh" --quiet --json >>"${TMP_RUNS}"; then
    echo "error: run ${i} failed" >&2
    exit 1
  fi
  echo >>"${TMP_RUNS}"
  sleep 1
done

python3 - "${OUT}" "${BENCHMARK_LABEL}" "${SSE_MODE}" "${STAMP}" "${BENCHMARK_NOTES}" "${console_git}" "${TMP_RUNS}" <<'PY'
import json
import sys
from pathlib import Path
from statistics import mean, median

out_path, label, mode, stamp, notes, console_git, runs_path = sys.argv[1:7]
runs_raw = Path(runs_path).read_text(encoding="utf-8").strip()
runs = [json.loads(block) for block in runs_raw.split("\n\n") if block.strip()]

def summarize(items):
    elapsed = [float(r["elapsed_seconds"]) for r in items]
    decomp = [int(r["decompressed_bytes"]) for r in items]
    events = [int(r.get("event_blocks_estimate", 0)) for r in items]
    return {
        "runs": len(items),
        "loaded_all": all(bool(r.get("loaded")) for r in items),
        "elapsed_seconds": {
            "min": round(min(elapsed), 2),
            "max": round(max(elapsed), 2),
            "avg": round(mean(elapsed), 2),
            "median": round(median(elapsed), 2),
        },
        "decompressed_bytes": {
            "min": min(decomp),
            "max": max(decomp),
            "avg": round(mean(decomp)),
            "median": round(median(decomp)),
        },
        "event_blocks_estimate": {
            "min": min(events),
            "max": max(events),
            "avg": round(mean(events)),
            "median": round(median(events)),
        },
    }

doc = {
    "label": label,
    "mode": mode,
    "timestamp": stamp,
    "notes": notes or None,
    "console_git": console_git or None,
    "runs": runs,
    "summary": summarize(runs),
}
Path(out_path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
s = doc["summary"]
print(f"Saved {out_path}")
print(f"  elapsed avg={s['elapsed_seconds']['avg']}s  median={s['elapsed_seconds']['median']}s")
print(f"  size avg={s['decompressed_bytes']['avg']} B  events~ avg={s['event_blocks_estimate']['avg']}")
print(f"  LOADED all runs: {s['loaded_all']}")
PY

echo "Compare later:" >&2
echo "  ./benchmark-sse.sh --compare <baseline.json> ${OUT}" >&2
