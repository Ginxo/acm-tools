# ACM console OOM stress test (host inventory)

Scripts to populate an ACM/MCE hub with labeled mock resources at high density and stress the **console backend** until it is **OOMKilled**. The scenario exercises a failure mode where non-admin users trigger excessive SubjectAccessReview (SSAR) work on the `/events` SSE stream when opening **Host inventory** (`/multicloud/infrastructure/environments`).

**Use only on disposable lab clusters.** All created resources are labeled `acm-oom-repro=true` and can be removed with `cleanup.sh`.

## Background

| Factor | Detail |
|--------|--------|
| **Trigger** | `none`-role users denied cluster-scoped `list`; event filtering may fall back to namespaced `list` plus per-object `get` SSARs — **O(N)** against the SSE watch cache |
| **UI surface** | Host inventory (CIM / InfraEnv agents) |
| **Mitigation** | Event-body compression and related console backend improvements |

## Lab shape (defaults)

| Parameter | Default |
|-----------|---------|
| Stub clusters | **25** (`CLUSTERS`) |
| Agents per InfraEnv | **10** (`HOSTS_PER_CLUSTER`) → **250 Agents** |
| Concurrent `none` users | **20** |
| Console memory ceiling | **3 GiB** on `console-mce-console` (set manually before measurement) |
| Label | `acm-oom-repro=true` |

At full density the hub holds roughly **~1,329** labeled objects across four layers (see [Resource layers](#resource-layers)).

## Requirements

- OpenShift hub with **ACM 2.13+** or **MCE** (tested around ACM 2.13 / OCP 4.16)
- `oc` logged in as **cluster-admin**
- `jq`, `htpasswd` (from `httpd-tools` or `apache2-utils`)
- Optional: **Node.js 18+** and Playwright for multi-user browser load (`host-inventory.mjs`)

## Quick start

```bash
cd performance/OOMKilled
chmod +x *.sh

# Review and adjust defaults
source ./env.sh

# 1. HTPasswd IDP with 20 none-role users (smoke-login once when prompted)
./apply-users.sh

# 2. Pause addon/hive/assisted controllers (reduces hub churn during apply)
./pause-controllers.sh

# 3. Apply layers A → D and run the gate (~10–20 min)
./apply-all.sh

# 4. Re-check counts after a short retention window
./gate.sh --retention

# 5. Cap console memory before measuring OOM
oc -n multicluster-engine set resources deploy/console-mce-console \
  --limits=memory=3Gi --requests=memory=512Mi
oc -n multicluster-engine rollout status deploy/console-mce-console

# 6. Optional: open Host inventory as many none users (Playwright)
npm ci
export CONSOLE_URL="https://console-openshift-console.apps.<your-cluster>"
node host-inventory.mjs   # uses ACM_NONE_USERS=01..20 by default

# 7. Tear down
./cleanup.sh
```

## Configuration

Edit `env.sh` or override exports before sourcing:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKDIR` | This directory | Runtime files (`users.htpasswd`, `controller-replicas.txt`) |
| `LABEL` | `acm-oom-repro` | Label key used for all mock resources |
| `CLUSTERS` | `25` | Number of mock managed clusters (`mock-mc-01` …) |
| `HOSTS_PER_CLUSTER` | `10` | Agents (and related CIM objects) per cluster |
| `MCE_NS` | `multicluster-engine` | Namespace of `console-mce-console` |
| `IMG` | `oom-img-stub` | Hive ClusterImageSet name |
| `ACM_IDP_NAME` | `oom-htpasswd` | OAuth identity provider name |
| `ACM_HTPASSWD_SECRET` | `oom-htpasswd-secret` | Secret in `openshift-config` |
| `ACM_NONE_PASS` | `OomLab1!` | Shared password for `acm-none-01`…`20` (**lab only**) |
| `ACM_NONE_USERS_COUNT` | `20` | Number of htpasswd users to create |

`users.htpasswd` and `controller-replicas.txt` are generated locally and listed in `.gitignore`.

## Scripts

| Script | Purpose |
|--------|---------|
| `env.sh` | Shared constants — **source** before other scripts |
| `apply-users.sh` | Create HTPasswd IDP + `acm-none-*` users (no ClusterRoleBindings) |
| `pause-controllers.sh` | Scale addon/hive/assisted controllers to 0; save replica counts |
| `restore-controllers.sh` | Restore controller replicas from `controller-replicas.txt` |
| `apply-layer-a.sh` | Namespaces, ManagedClusters, ManagedClusterInfo, 8 add-ons, 3 policies |
| `apply-layer-b.sh` | Hive: ClusterImageSet, ClusterDeployment, MachinePools, stub secrets |
| `apply-layer-c.sh` | CIM: InfraEnv, Agents, BareMetalHost, NMStateConfig, ACI, AgentMachine |
| `apply-layer-d.sh` | PolicyReports, CSRs, policy status pressure |
| `apply-all.sh` | Pause → layers A–D → `gate.sh --retention` |
| `gate.sh` | Verify composite counts for layers A–D (`--retention` waits 120s and re-checks) |
| `cleanup.sh` | Delete labeled resources, restore controllers, remove optional memory limit |
| `host-inventory.mjs` | Playwright: log in as multiple `none` users and open Host inventory |

## Resource layers

| Layer | Resources | Count @ N=25, HOSTS=10 |
|-------|-----------|------------------------|
| **A** — mock parity | NS, MC, MCI, MCA, Policy | 25 / 25 / 200 / 3 |
| **B** — Hive | CD, MP×2, CP, Secrets, ClusterImageSet | 25 / 50 / 25 / ≥100 / 1 |
| **C** — CIM / host inventory | InfraEnv, Agent, BMH, NMStateConfig, ACI, AgentMachine | 25 / **250** / 250 / 250 / 25 / 250 |
| **D** — pressure | PolicyReport, CSR | 25 / 25 |

`Agent` is the dominant SSAR multiplier: **250 agents × 20 users × up to 3 SSARs on SSE connect**.

Gate output example:

```
GATE_A: MC=25 MCI=25 MCA=200 POL=3 NS=25  → need 25/25/200/3/25
GATE_B: CD=25 MP=50 CP=25 SEC=125         → need 25/50/25/≥100
...
GATE: PASS
```

## Measurement notes

1. **Before vs after fix** — Run the same density with a console image **without** event compression, capture RSS/OOM, then repeat with an improved build.
2. **Memory limit** — OOM is per-pod; with 2 `console-mce-console` replicas load may split. Scale to 1 replica for a controlled run if needed.
3. **Controllers** — Keep controllers paused during apply/measurement; `cleanup.sh` restores them.
4. **Secrets in layers B/C** — All Hive/CIM credentials are **stub** data (`e30=` / empty docker config). No real pull secrets or kubeconfigs are committed.

## Playwright load test

```bash
npm ci
export CONSOLE_URL="https://..."
export ACM_NONE_PASS='OomLab1!'           # or your override
export ACM_NONE_USERS="01 02 03 ... 20"   # space-separated suffixes or full usernames
node host-inventory.mjs
```

Sessions stay open until Ctrl+C so you can observe memory while pages remain on Host inventory.

## Cleanup

```bash
./cleanup.sh
```

Deletes all resources labeled `acm-oom-repro=true`, restores controller replica counts, and optionally clears the 3 GiB memory limit on `console-mce-console`.

To remove the HTPasswd IDP manually, edit the cluster OAuth configuration and delete the `oom-htpasswd` identity provider.

## License

This directory is part of [acm-tools](https://github.com/Ginxo/acm-tools) and is licensed under the Apache License 2.0 (see repository root).
