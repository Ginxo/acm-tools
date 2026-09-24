# Contract performance benchmarks

Black-box performance harness for the ACM console backend on `https://localhost:4000`, driven the YAML catalog at [contract validation tests](/catalog).

Measures per-endpoint **response time**, backend process **CPU** and **memory (RSS)**, and supports before/after JSON comparison when switching backend versions.

## Prerequisites

- Backend running locally (`npm run plugins` or `npm run start:backend`) on port **4000**
- `oc login` (token via `oc whoami -t`)
- Python 3 with **PyYAML** (`python3 -c "import yaml"`)
- `curl`, `ss` or `lsof` (process sampler)
- Optional: `jq` (sampler summary in `benchmark-contract.sh`)

Quick check:

```bash
curl -sk https://localhost:4000/ping
```

## Quick start

```bash
cd performance/contract
cp env.example .env   # optional overrides

# Baseline (3 full catalog passes)
BENCHMARK_LABEL=baseline BENCHMARK_RUNS=3 CONSOLE_REPO=/path/to/console ./benchmark-contract.sh

# After code change — restart backend first
BENCHMARK_LABEL=after-my-fix BENCHMARK_NOTES="describe change" ./benchmark-contract.sh

# Compare (Markdown output for GitHub PRs — copy stdout)
./benchmark-contract.sh --compare \
  generated/benchmarks/baseline-*.json \
  generated/benchmarks/after-my-fix-*.json

# Plain-text output (legacy)
./benchmark-contract.sh --compare baseline.json after.json --format text
```

## What is measured

| Dimension | Scope | Notes |
|-----------|-------|-------|
| Response time | Per case | Wall-clock to full body (REST) or LOADED (SSE) |
| Response size | Per case | Decompressed bytes |
| CPU % | Per run | Avg/peak of backend PID on `:4000` |
| Memory RSS | Per run | Start/peak/end/avg of backend PID |
| Total elapsed | Per run | Sum of all case timings |

Catalog: **104 cases** (excludes `80-negative.yaml` and `auth: invalid`). Cases with `alsoMulticloud: true` run twice (plain + `/multicloud` prefix).

## Progress output

Runs are long (~15–20 min per pass). By default (`PERF_VERBOSE=true`) stderr shows:

```
[run 1/3] [47/104] (45.2%) → GET /hub  (id=hub, kind=rest)
[run 1/3] [47/104] (45.2%) ✓ hub  0.34s  200  1.0 KiB
```

SSE cases emit periodic heartbeats until LOADED. Set `PERF_VERBOSE=false` for CI-style quiet runs.

## Subset while developing

```bash
CONTRACT_GROUP=probes BENCHMARK_RUNS=1 ./benchmark-contract.sh
```

Groups match catalog YAML: `probes`, `auth`, `kube-proxy`, `sse`, `websocket`, `long-tail`, etc.

## Sync catalog from upstream

When the contract catalog changes in the console Go migration tree:

```bash
SRC=/path/to/console/ACM-42568_go_migration/catalog
DST=performance/contract/catalog
for f in "$SRC"/*.yaml; do
  base=$(basename "$f")
  [[ "$base" == "80-negative.yaml" || "$base" == "watched-resources.yaml" ]] && continue
  cp "$f" "$DST/"
done
```

## Report JSON

Written to `generated/benchmarks/{BENCHMARK_LABEL}-{timestamp}.json`:

- `runs[]` — each full catalog pass with `cases[]` and `process` sampler stats
- `summary.cases` — per-case elapsed/bytes min/avg/median/max across runs
- `summary.process` — CPU and RSS aggregates
- `summary.total_elapsed_seconds` — full-pass duration stats

## Environment variables

See [`env.example`](env.example). Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTRACT_BACKEND_URL` | `https://localhost:4000` | Backend URL |
| `BENCHMARK_LABEL` | `run` | Output file prefix |
| `BENCHMARK_RUNS` | `3` | Repeat count |
| `BENCHMARK_WARMUP` | `false` | Discard first pass |
| `PERF_VERBOSE` | `true` | X/Y progress traces |
| `PERF_PROGRESS_INTERVAL` | `5` | SSE heartbeat seconds |
| `CONSOLE_REPO` | — | Git SHA in metadata |

## Scripts

| Script | Role |
|--------|------|
| `benchmark-contract.sh` | Main entry: run N passes, aggregate JSON, `--compare` |
| `measure-contract.py` | Execute catalog cases with timing |
| `process-sampler.py` | Sample CPU/RSS of process on port 4000 |
| `load_catalog.py` | Parse/expand YAML catalog |
| `compare-contract-benchmarks.py` | Before/after delta report |

## Fair comparison tips

1. Restart the backend between baseline and after runs.
2. Use the same hub (`oc login`) and `.env` backend config.
3. Prefer **median** total elapsed and per-case times for claims.
4. RSS peak should be stable unless memory behavior changed.
5. `BENCHMARK_WARMUP=true` discards the first full pass (cache warm-up).

## Related

- SSE-only benchmarks (large fleet): [`../events/README.md`](../events/README.md)
- Contract validation source: `ACM-42568_go_migration` in the console repo
