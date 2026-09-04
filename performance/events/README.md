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

Equivalent to browser `GET …/multicloud/events` until `LOADED`:

```bash
# Recommended: port-forward to console backend + Bearer token from oc login
./measure-sse.sh

# Via OCP console plugin (needs browser cookie session; often fails with oc token only)
# SSE_MODE=proxy ./measure-sse.sh

# Save decompressed stream:
# SSE_SAVE=generated/events-stream.bin ./measure-sse.sh
```

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
| `measure-sse.sh` / `measure-sse.py` | SSE size until `LOADED` |
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
