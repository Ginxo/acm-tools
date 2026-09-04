# acm-tools

Public collection of scripts and utilities for **operating, testing, and troubleshooting** [Red Hat Advanced Cluster Management (ACM)](https://www.redhat.com/en/technologies/management/advanced-cluster-management) and **Multicluster Engine (MCE)** hubs.

The focus is practical, repeatable workflows you can run from a laptop against a lab cluster: populate mock data, measure console behaviour, and validate fixes — without depending on proprietary console source code.

> **Lab use only.** Most tools create or modify cluster resources. Run them on disposable environments with `cluster-admin` access, and clean up when finished.

## What is here today

```
acm-tools/
└── performance/
    ├── events/      # Large mock fleet + SSE /events measurement
    └── OOMKilled/   # High-density CIM scenario + console OOM stress test
```

| Directory | Purpose |
|-----------|---------|
| [`performance/events`](performance/events/) | Build a scalable mock fleet (~750 SNOs by default), add Hive/governance density, and measure the ACM console **`/multicloud/events`** SSE payload and pod memory. |
| [`performance/OOMKilled`](performance/OOMKilled/) | Maximum-density **host inventory** scenario: mock clusters, agents, `none`-role users, and Playwright load to reproduce console backend memory pressure. |

Each subdirectory has its own README with prerequisites, configuration, and step-by-step workflows.

### Common requirements

- `oc` logged in to the target hub
- `cluster-admin` (or equivalent) for apply/cleanup scripts
- Bash; Python 3 where noted; Node.js for Playwright-based tools

Resources created by these scripts are labeled consistently (`acm-perf-repro` or `acm-oom-repro`) so they can be removed in one pass. Never commit `.env`, generated YAML, or cluster dumps.

## Roadmap

This repository is meant to grow into a broader **ACM operations toolkit**, for example:

- **Performance & scale** — more console and hub benchmarks, repeatable density presets
- **Day-2 operations** — health checks, inventory helpers, upgrade readiness
- **Governance & fleet** — templates for policies, placements, and mock fleets
- **Debugging** — targeted collectors and sanity gates for common support scenarios

New tools should stay **issue-agnostic**: generic names, no customer-specific data, and clear cleanup paths. Contributions and ideas are welcome via issues and pull requests.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
