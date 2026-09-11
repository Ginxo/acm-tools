# ACM console events & fleet performance tools

Scripts to populate an ACM/MCE hub with a **large mock fleet** (~750 single-node OpenShift clusters by default), add Hive and governance density layers, and **measure console backend behaviour** — especially the `/multicloud/events` SSE stream size and pod memory use.

**Use only on disposable lab clusters.** Resources are labeled `acm-perf-repro=true` and removed with `cleanup.sh`.

## What this measures

| Area | Tooling |
|------|---------|
| **SSE payload** | `measure-sse.sh` — decompressed bytes until a `LOADED` event (optional reference comparison via `SSE_REFERENCE_BYTES`) |
| **Pod memory** | `observe-memory.sh`, `set-console-memory-8gi.sh`, `enable-console-logging.sh` |
| **Hub density** | Mock `ManagedCluster` fleet, Hive stubs (layer B), governance reports (layer D) |

## Requirements

- OpenShift hub with **ACM 2.17+** or **MCE** (tested with ACM-style console deployments)
- `oc` logged in as **cluster-admin**
- Python 3 (optional: `pip install pyyaml` for readable YAML from `generate-mock-fleet.py`)
- `jq` recommended for route discovery

## Configuration

```bash
cd performance/events
cp env.example .env    # optional overrides — never commit .env
chmod +x *.sh *.py
```

Key defaults (override in `.env` or environment):

| Variable | Default | Purpose |
|----------|---------|---------|
| `CLUSTER_PREFIX` | `mock-sno` | ManagedCluster name prefix |
| `CLUSTER_WIDTH` | `4` | Zero-padded index width (`mock-sno-0001`) |
| `CLUSTER_SET_NAME` | `perf-fleet` | ManagedClusterSet for governance |
| `GOVERNANCE_NS` | `perf-governance` | Policies and placements |
| `FLEET_IMAGE_SET` | `perf-img-stub` | Hive ClusterImageSet name |
| `FLEET_BASE_DOMAIN` | `perf.example.com` | Hive base domain (fictitious) |
| `BATCH_SIZE` / `BATCH_SLEEP` | `10` / `2` | Apply batching |
| `CONSOLE_BACKEND` | `auto` | `auto` \| `mce` \| `acm` — picks console Node backend |
| `SSE_MODE` | `direct` | `plugins` \| `local` \| `plugin-dev` \| `direct` \| `proxy` — see §7 |
| `DEV_CONSOLE_URL` | — | Local OCP console base for `plugins` mode (default `http://127.0.0.1:9000`) |
| `CONSOLE_PORT` | `9000` | Local OpenShift console from `npm run plugins` |
| `MCE_PORT` | `3002` | MCE plugin webpack port (`plugin-dev` mode) |
| `SSE_COOKIE` | — | Browser session cookie if `plugins` mode returns HTTP 401 |
| `CONSOLE_PLUGIN` | `mce` | `mce` or `acm` plugin segment in proxy URL |
| `LOCAL_BACKEND_PORT` | `4000` | Dev backend port when `SSE_MODE=local` |
| `SSE_LOCAL_URL` | — | Full URL override for local mode |
| `SSE_REFERENCE_BYTES` | `300006221` | Optional reference size for `measure-sse.py` output |

Label key: **`acm-perf-repro=true`** on all created objects.

## Recommended workflow

### 1. Preflight

```bash
./preflight.sh
```

Shows hub identity, ACM/MCE version hints, and which console deployment was auto-selected (excludes `acm-cli-downloads` sidecars).

### 2. Console memory limit (optional)

Match a high-memory production ceiling before testing:

```bash
./set-console-memory-8gi.sh
```

### 3. Backend memory logging (optional)

```bash
./enable-console-logging.sh
```

### 4. Governance bundle

ClusterSet, placement, and three policies:

```bash
./apply-governance.sh
```

### 5. Mock fleet (layer A) — scale gradually

| Preset | Clusters | Command |
|--------|----------|---------|
| smoke | 10 | `./apply-batch.sh --preset smoke` |
| small | 50 | `./apply-batch.sh --preset small` |
| medium | 250 | `./apply-batch.sh --preset medium` |
| target | 750 | `./apply-batch.sh --preset target` |
| stretch | 1500 | `./apply-batch.sh --preset stretch` |

Verify after each stage:

```bash
./verify-counts.sh
```

### 6. Density layers B + D

After the fleet exists:

```bash
# Hive: ClusterDeployment, MachinePool, ClusterProvision, stub secrets
./apply-layer-b.sh --preset target

# PolicyReport, CSR, bulk policy status
./apply-layer-d.sh --preset target
# Faster without CSRs:
# ./apply-layer-d.sh --preset target --skip-csr

./gate-density.sh 1 750
```

Per-cluster extras (layers B + D):

| Resource | Count |
|----------|------:|
| Secrets (Hive) | 5 |
| ClusterProvision | 1 |
| MachinePool | 2 |
| ClusterDeployment | 1 |
| PolicyReport | 1 |
| CSR | 1 (optional) |

### 7. Measure SSE

The console backend exposes **`GET /events`** (the browser loads **`/multicloud/events`** via the OCP Console plugin proxy). All modes measure the same **decompressed** payload until a `LOADED` event.

#### Measurement modes

| `SSE_MODE` | Target | Use case |
|------------|--------|----------|
| **`plugins`** | `http://localhost:9000/api/proxy/plugin/mce/console/multicloud/events` | **`npm run plugins`** — same URL path as the browser Network tab |
| **`local`** | `https://127.0.0.1:4000/events` | Backend only (skips console + webpack proxy; good for isolating backend code) |
| **`plugin-dev`** | `https://localhost:3002/multicloud/events` | MCE webpack dev-server proxy (not the integrated console URL) |
| **`direct`** (default on hub) | Port-forward → hub `console-mce-console` | Production pod on cluster |
| **`proxy`** | Remote OCP console route | Hub console route (remote cluster) |

#### A) Hub pod (default)

```bash
oc login https://api.emingora-acm-43194.dev09.red-chesterfield.com:6443 ...
SSE_MAX_SECONDS=600 ./measure-sse.sh
# same as: SSE_MODE=direct ./measure-sse.sh
```

#### B) Local dev with `npm run plugins` (browser-like — **use this**)

Terminal 1 — from your **console** clone:

```bash
cd /path/to/console
npm ci
npm run setup          # backend/.env for the hub under test
npm run plugins        # backend :4000, MCE plugin :3002, OCP console :9000
```

Open the console at **http://localhost:9000**, log in, and confirm ACM/MCE loads. In DevTools → Network the events request looks like:

`/api/proxy/plugin/mce/console/multicloud/events`

Terminal 2 — measure that **same path** (not `:4000/events` directly):

```bash
cd performance/events
oc login …   # same hub as backend/.env

SSE_MODE=plugins SSE_MAX_SECONDS=600 ./measure-sse.sh
# MCE plugin (default): CONSOLE_PLUGIN=mce
# ACM plugin instead:   CONSOLE_PLUGIN=acm SSE_MODE=plugins ./measure-sse.sh
```

If you get **HTTP 401**, the local console proxy expects a browser session cookie. Log in at http://localhost:9000, copy the `Cookie` header from the `/multicloud/events` request in DevTools, then:

```bash
SSE_COOKIE='openshift-session-token=…; csrf-token=…' SSE_MODE=plugins ./measure-sse.sh
```

#### B2) Local backend only (`SSE_MODE=local`)

Skips the OpenShift Console and webpack proxy — measures **`https://127.0.0.1:4000/events`** directly. Use this to isolate **backend code** changes without console overhead; payload size should match `plugins` mode, elapsed time may differ.

```bash
npm run start:backend   # or keep npm run plugins running (backend still on :4000)
SSE_MODE=local SSE_MAX_SECONDS=600 ./measure-sse.sh
```

#### B3) Webpack dev server only (`SSE_MODE=plugin-dev`)

`https://localhost:3002/multicloud/events` — webpack → backend. **Not** the URL used when browsing via http://localhost:9000.

```bash
SSE_MODE=plugin-dev SSE_MAX_SECONDS=600 ./measure-sse.sh
```

#### C) OpenShift Console route (plugin proxy)

```bash
CONSOLE_URL=https://console-openshift-console.apps.emingora-acm-43194.dev09.red-chesterfield.com \
CONSOLE_PLUGIN=mce \
SSE_MODE=proxy \
SSE_MAX_SECONDS=600 \
./measure-sse.sh
```

If you get HTTP 401/403, the plugin proxy may require a browser session cookie; use `direct` or `local` for reliable automation. Payload size should still align with the browser Network tab when auth succeeds.

#### Comparison checklist

Run all three against the **same hub state** (after `gate-density.sh` passes):

```bash
# 1) Hub pod
SSE_MAX_SECONDS=600 ./measure-sse.sh | tee results-hub-direct.txt

# 2) Local backend (start npm run start:backend first)
SSE_MODE=local SSE_MAX_SECONDS=600 ./measure-sse.sh | tee results-local.txt

# 3) Console route proxy
CONSOLE_URL=https://console-openshift-console.apps.emingora-acm-43194.dev09.red-chesterfield.com \
SSE_MODE=proxy SSE_MAX_SECONDS=600 ./measure-sse.sh | tee results-console-proxy.txt
```

Record: `Elapsed`, `Decompressed size`, `Event blocks (~)`, `LOADED seen`.

Optional:

```bash
# Save decompressed stream for diff:
# SSE_SAVE=generated/events-hub.bin ./measure-sse.sh
# SSE_MODE=local SSE_SAVE=generated/events-local.bin ./measure-sse.sh
```

#### Notes

- Default encoding is **identity** (not gzip) to avoid `http.client` stalls; decompressed size matches browser gzip metric. Set `SSE_GZIP=1` only if you also need wire size.
- Kill stale port-forwards before `direct` mode: `pkill -f 'port-forward.*19443:3000' || true`
- `local` mode does not use port-forward; ensure nothing else binds `4000`.

### 7.1 Local backend performance iteration (before / after)

Use this loop when changing **@stolostron/console** backend code and measuring `/events` impact on the **same hub fleet**.

**Terminal 1 — local backend** (leave running between runs, or restart deliberately — see below):

```bash
cd /path/to/console
npm run setup
npm run start:backend
```

Wait until watches populate the in-memory cache (on a 750-cluster repro hub, often **2–5 minutes** after startup). Optional sanity check:

```bash
curl -sk -H "Authorization: Bearer $(oc whoami -t)" https://127.0.0.1:4000/ping
```

**Terminal 2 — record baseline** (from acm-tools):

```bash
cd performance/events
oc login …   # same hub as backend/.env

# Browser-like path (npm run plugins):
BENCHMARK_LABEL=baseline \
BENCHMARK_RUNS=3 \
BENCHMARK_WARMUP=true \
CONSOLE_REPO=/path/to/console \
SSE_MODE=plugins \
./benchmark-sse.sh

# Or backend-only isolation:
# SSE_MODE=local ./benchmark-sse.sh
```

This writes `generated/benchmarks/baseline-<timestamp>.json` with min/avg/median/max for **elapsed**, **decompressed bytes**, and **event blocks**.

**Apply your backend changes**, then restart the backend for a fair comparison:

```bash
# Terminal 1: Ctrl+C, then
npm run start:backend
# wait for cache warm again (same duration as baseline)
```

**Record after:**

```bash
BENCHMARK_LABEL=after-my-fix \
BENCHMARK_RUNS=3 \
BENCHMARK_WARMUP=true \
BENCHMARK_NOTES="describe your change here" \
CONSOLE_REPO=/path/to/console \
SSE_MODE=local \
./benchmark-sse.sh
```

**Compare:**

```bash
./benchmark-sse.sh --compare \
  generated/benchmarks/baseline-20260101T120000Z.json \
  generated/benchmarks/after-my-fix-20260101T123000Z.json
```

Negative **elapsed** delta = faster. **Decompressed size** should stay equal unless you changed what resources are streamed.

#### Single-shot (no JSON archive)

```bash
SSE_MODE=local SSE_MAX_SECONDS=600 ./measure-sse.sh
SSE_MODE=local ./measure-sse.sh --json > /tmp/sse-run.json
```

#### Fair comparison tips

| Scenario | What to do |
|----------|------------|
| **Code change (SSAR, batching, compression)** | Restart backend; same warm-up wait; `BENCHMARK_WARMUP=true`; compare median elapsed |
| **Steady-state repeat load** | Keep backend up; `BENCHMARK_WARMUP=true` discards first run; runs 2–N reflect warm cache |
| **Payload regression** | If `decompressed_bytes` changes but hub unchanged, you altered event content — investigate before claiming speedup |
| **Hub drift** | Re-run `./gate-density.sh` if fleet changed between baseline and after |

#### Metrics to track

| Metric | Field | Goal when optimizing speed |
|--------|-------|----------------------------|
| Time to `LOADED` | `summary.elapsed_seconds.median` | Lower |
| SSE payload | `summary.decompressed_bytes.avg` | Unchanged (same hub) |
| Event count | `summary.event_blocks_estimate.avg` | Unchanged |

Optional backend logs while benchmarking: `LOG_MEMORY=true` (see `enable-console-logging.sh` for hub pod; set in local `backend/.env` for dev).

### 8. Policy status (if layer D was skipped)

```bash
./patch-policy-status.py --start 1 --end 750
```

### 9. Observe pod memory

In a separate terminal:

```bash
./observe-memory.sh
```

### 10. Cleanup

```bash
./cleanup.sh
```

## Per-cluster inventory (layer A)

For each `mock-sno-NNNN`:

| Resource | Count |
|----------|------:|
| Namespace | 1 |
| ManagedCluster | 1 |
| ManagedClusterInfo | 1 (often created by OCM when MC is applied) |
| ManagedClusterAddOn | 8 |

## Offline YAML generation

`apply-batch.sh` writes batches under `generated/` (gitignored). To preview without applying:

```bash
python3 generate-mock-fleet.py --start 1 --end 10 > generated/sample.yaml
```

## Scripts reference

| Script | Role |
|--------|------|
| `lib.sh` | Shared constants, console auto-detection, fleet helpers |
| `preflight.sh` | Hub and console pre-checks |
| `set-console-memory-8gi.sh` | Raise console pod memory limit to 8 GiB |
| `enable-console-logging.sh` | Enable `LOG_MEMORY=true` on console deployment |
| `generate-mock-fleet.py` | Emit fleet YAML/JSON |
| `apply-batch.sh` | Batched apply + status patch |
| `patch-fleet-status.sh` | Patch ManagedCluster / ManagedClusterInfo status |
| `apply-governance.sh` | Apply `governance/fleet-base.yaml` |
| `apply-layer-b.sh` | Hive density stubs |
| `apply-layer-d.sh` | PolicyReport, CSR, policy status pressure |
| `gate-density.sh` | Count gate for layers A / B / D |
| `measure-sse.sh` / `measure-sse.py` | Single SSE measurement until `LOADED` |
| `benchmark-sse.sh` | Repeat runs + JSON for before/after perf comparison |
| `compare-sse-benchmarks.py` | Print delta between two benchmark JSON files |
| `patch-policy-status.py` | Bulk policy compliance status |
| `observe-memory.sh` | Continuous pod memory watch |
| `verify-counts.sh` | Human-readable resource totals |
| `cleanup.sh` | Delete all `acm-perf-repro=true` resources |

## Security notes

- Hive and CIM credentials created by layers B/C are **stub** data only (`e30=` / empty docker config).
- Never commit `.env` or files under `generated/` (except keeping the directory via `.gitkeep` if desired).
- All cluster URLs use fictitious `*.perf.example.com` domains unless you override them locally.

## License

Part of [acm-tools](https://github.com/Ginxo/acm-tools) — Apache License 2.0 (see repository root).
