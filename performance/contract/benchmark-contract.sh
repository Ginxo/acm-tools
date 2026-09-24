#!/usr/bin/env bash
# Copyright Contributors to the Open Cluster Management project
# Run contract catalog performance benchmarks and save JSON for comparison.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<EOF
Usage: $(basename "$0")
       $(basename "$0") --compare BASELINE.json AFTER.json [--format markdown|text]

Run contract catalog performance benchmarks against localhost:4000.

Compare output is Markdown by default (ready to paste into a GitHub PR).

Environment:
  CONTRACT_BACKEND_URL   Backend URL (default: https://localhost:4000)
  CONTRACT_TOKEN         Bearer token (default: oc whoami -t)
  CONTRACT_GROUP         Filter catalog group (optional)
  BENCHMARK_LABEL        Run label (default: run)
  BENCHMARK_RUNS         Repeat count (default: 3)
  BENCHMARK_WARMUP       Discard first run if true (default: false)
  BENCHMARK_DIR          Output dir (default: generated/benchmarks)
  BENCHMARK_NOTES        Free-text note in JSON
  CONSOLE_REPO           Console clone for git rev metadata
  PERF_VERBOSE           Progress traces (default: true)
  PERF_SAMPLE_INTERVAL   Process sampler interval seconds (default: 1)
  LOCAL_BACKEND_PORT     Port for process sampler (default: 4000)

Examples:
  BENCHMARK_LABEL=baseline BENCHMARK_RUNS=3 ./benchmark-contract.sh
  ./benchmark-contract.sh --compare generated/benchmarks/baseline-*.json generated/benchmarks/after-*.json
  ./benchmark-contract.sh --compare baseline.json after.json --format text
EOF
}

load_env

BENCHMARK_LABEL="${BENCHMARK_LABEL:-run}"
BENCHMARK_RUNS="${BENCHMARK_RUNS:-3}"
BENCHMARK_WARMUP="${BENCHMARK_WARMUP:-false}"
BENCHMARK_DIR="${BENCHMARK_DIR:-${SCRIPT_DIR}/generated/benchmarks}"
BENCHMARK_NOTES="${BENCHMARK_NOTES:-}"
CONSOLE_REPO="${CONSOLE_REPO:-}"
PERF_SAMPLE_INTERVAL="${PERF_SAMPLE_INTERVAL:-1}"
LOCAL_BACKEND_PORT="${LOCAL_BACKEND_PORT:-4000}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--compare" ]]; then
  [[ $# -ge 3 ]] || {
    usage >&2
    exit 1
  }
  exec python3 "${SCRIPT_DIR}/compare-contract-benchmarks.py" "${@:2}"
fi

require_oc
preflight_backend

mkdir -p "${BENCHMARK_DIR}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BENCHMARK_DIR}/${BENCHMARK_LABEL}-${STAMP}.json"
TMP_RUNS="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_RUNS}"
}
trap cleanup EXIT

console_git=""
if [[ -n "${CONSOLE_REPO}" && -d "${CONSOLE_REPO}/.git" ]]; then
  console_git="$(git -C "${CONSOLE_REPO}" rev-parse --short HEAD 2>/dev/null || true)"
fi

CASE_TOTAL="$(case_count)"
echo "Benchmark label: ${BENCHMARK_LABEL}  runs: ${BENCHMARK_RUNS}  cases: ${CASE_TOTAL}" >&2
echo "Backend: ${CONTRACT_BACKEND_URL}" >&2
echo "Output: ${OUT}" >&2

export CONTRACT_TOKEN="${CONTRACT_TOKEN:-$(contract_token)}"

if [[ "${BENCHMARK_WARMUP}" == true ]]; then
  echo "Warmup run (discarded) ..." >&2
  SAMPLER_OUT="${TMP_RUNS}/warmup-sampler.json"
  python3 "${SCRIPT_DIR}/process-sampler.py" \
    --port "${LOCAL_BACKEND_PORT}" \
    --interval "${PERF_SAMPLE_INTERVAL}" \
    --output "${SAMPLER_OUT}" &
  SAMPLER_PID=$!
  python3 "${SCRIPT_DIR}/measure-contract.py" --run-all --run-index 0 --run-total "${BENCHMARK_RUNS}" \
    --output "${TMP_RUNS}/warmup.json" ${CONTRACT_GROUP:+--group "${CONTRACT_GROUP}"} || true
  kill -TERM "${SAMPLER_PID}" 2>/dev/null || true
  wait "${SAMPLER_PID}" 2>/dev/null || true
fi

for i in $(seq 1 "${BENCHMARK_RUNS}"); do
  RUN_JSON="${TMP_RUNS}/run-${i}.json"
  SAMPLER_OUT="${TMP_RUNS}/sampler-${i}.json"
  echo "Starting run ${i}/${BENCHMARK_RUNS} ..." >&2
  python3 "${SCRIPT_DIR}/process-sampler.py" \
    --port "${LOCAL_BACKEND_PORT}" \
    --interval "${PERF_SAMPLE_INTERVAL}" \
    --output "${SAMPLER_OUT}" &
  SAMPLER_PID=$!
  python3 "${SCRIPT_DIR}/measure-contract.py" --run-all \
    --run-index "${i}" \
    --run-total "${BENCHMARK_RUNS}" \
    --output "${RUN_JSON}" \
    ${CONTRACT_GROUP:+--group "${CONTRACT_GROUP}"}
  kill -TERM "${SAMPLER_PID}" 2>/dev/null || true
  wait "${SAMPLER_PID}" 2>/dev/null || true
  PROCESS_JSON="$(cat "${SAMPLER_OUT}")"
  python3 - "${RUN_JSON}" "${PROCESS_JSON}" <<'PY'
import json
import sys
from pathlib import Path

run_path, process_path = sys.argv[1], sys.argv[2]
run = json.loads(Path(run_path).read_text(encoding="utf-8"))
process = json.loads(process_path)
run["process"] = process
Path(run_path).write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
PY
  if command -v jq >/dev/null 2>&1; then
    pid="$(jq -r '.pid' "${SAMPLER_OUT}")"
    cpu_avg="$(jq -r '.cpu_percent.avg' "${SAMPLER_OUT}")"
    cpu_peak="$(jq -r '.cpu_percent.peak' "${SAMPLER_OUT}")"
    rss_peak="$(jq -r '.memory_rss_bytes.peak' "${SAMPLER_OUT}")"
    rss_mib=$((rss_peak / 1024 / 1024))
    echo "[run ${i}/${BENCHMARK_RUNS}] process pid=${pid}  cpu avg=${cpu_avg}% peak=${cpu_peak}%  rss peak=${rss_mib} MiB" >&2
  fi
  sleep 1
done

python3 - "${OUT}" "${BENCHMARK_LABEL}" "${STAMP}" "${BENCHMARK_NOTES}" "${console_git}" "${CONTRACT_BACKEND_URL}" "${CASE_TOTAL}" "${TMP_RUNS}" "${BENCHMARK_RUNS}" <<'PY'
import json
import sys
from pathlib import Path
from statistics import mean, median

out_path, label, stamp, notes, console_git, backend_url, case_total, tmp_runs, runs_total = sys.argv[1:10]
runs_total = int(runs_total)
runs = []
for i in range(1, runs_total + 1):
    runs.append(json.loads(Path(tmp_runs, f"run-{i}.json").read_text(encoding="utf-8")))

def stat_values(values):
    values = list(values)
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "median": 0}
    if isinstance(values[0], float):
        return {
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "avg": round(mean(values), 3),
            "median": round(median(values), 3),
        }
    return {
        "min": min(values),
        "max": max(values),
        "avg": round(mean(values)),
        "median": round(median(values)),
    }


def median_bool(values):
    values = list(values)
    if not values:
        return False
    return bool(median(values))

case_ids = sorted({c["id"] for run in runs for c in run["cases"]})
case_summary = {}
for case_id in case_ids:
    case_runs = [next(c for c in run["cases"] if c["id"] == case_id) for run in runs]
    elapsed = [float(c["elapsed_seconds"]) for c in case_runs]
    bytes_ = [int(c.get("response_bytes", 0)) for c in case_runs]
    case_summary[case_id] = {
        "elapsed_seconds": stat_values(elapsed),
        "elapsed_ms": stat_values([round(v * 1000, 1) for v in elapsed]),
        "response_bytes": stat_values(bytes_),
        "success": median_bool([bool(c.get("success")) for c in case_runs]),
        "skipped": median_bool([bool(c.get("skipped")) for c in case_runs]),
        "http_status": stat_values([int(c.get("http_status", 0)) for c in case_runs]),
    }

cpu_avg = [float(run["process"]["cpu_percent"]["avg"]) for run in runs if "process" in run]
cpu_peak = [float(run["process"]["cpu_percent"]["peak"]) for run in runs if "process" in run]
rss_peak = [int(run["process"]["memory_rss_bytes"]["peak"]) for run in runs if "process" in run]
total_elapsed = [float(run["total_elapsed_seconds"]) for run in runs]

outcomes = {"ok": 0, "skip": 0, "fail": 0}
for meta in case_summary.values():
    if meta["skipped"]:
        outcomes["skip"] += 1
    elif meta["success"]:
        outcomes["ok"] += 1
    else:
        outcomes["fail"] += 1

doc = {
    "label": label,
    "backend_url": backend_url,
    "console_git": console_git or None,
    "timestamp": stamp,
    "notes": notes or None,
    "case_count": int(case_total),
    "runs": runs,
    "summary": {
        "cases": case_summary,
        "outcomes": outcomes,
        "process": {
            "cpu_percent": {"avg": stat_values(cpu_avg), "peak": stat_values(cpu_peak)},
            "memory_rss_peak_bytes": stat_values(rss_peak),
        },
        "total_elapsed_seconds": stat_values(total_elapsed),
    },
}
Path(out_path).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
print(f"Saved {out_path}")
s = doc["summary"]["total_elapsed_seconds"]
print(f"  total elapsed median={s['median']}s  avg={s['avg']}s")
PY

echo "Benchmark complete: label=${BENCHMARK_LABEL}  runs=${BENCHMARK_RUNS}  cases=${CASE_TOTAL}  output=${OUT}" >&2
echo "Compare later:" >&2
echo "  ./benchmark-contract.sh --compare <baseline.json> ${OUT}" >&2
