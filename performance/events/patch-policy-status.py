#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Patch Policy status.status[] for mock fleet clusters (fixed compliance ratio presets)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# 0-based cluster indices with NonCompliant status (preset distribution for large fleets)
NONCOMPLIANT: list[set[int]] = [
    {3, 18, 23, 55, 60, 80, 93},
    {1, 33, 43, 68},
    {5, 33, 69, 20, 45, 32},
]

# Pending clusters — Policy API only allows Compliant | Pending | NonCompliant.
PENDING: list[set[int]] = [
    {20, 71, 22, 98},
    {44, 85, 86},
    {23, 36},
]

VALID_COMPLIANT = frozenset({"Compliant", "Pending", "NonCompliant"})

POLICY_NAMES = ["perf-policy1", "perf-policy2", "perf-policy3"]
GOVERNANCE_NS = os.environ.get("GOVERNANCE_NS", "perf-governance")
REPRO_LABEL = os.environ.get("REPRO_LABEL", "acm-perf-repro=true")


def run_oc(args: list[str]) -> str:
    result = subprocess.run(["oc", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def list_repro_clusters(prefix: str, width: int, start: int, end: int) -> list[tuple[int, str]]:
    names: list[str] = []
    try:
        out = run_oc(["get", "managedcluster", "-l", REPRO_LABEL, "-o", "name"])
        names = [line.split("/", 1)[-1] for line in out.splitlines() if line.strip()]
    except RuntimeError:
        names = []

    if not names:
        out = run_oc(["get", "managedcluster", "-o", "name"])
        names = [line.split("/", 1)[-1] for line in out.splitlines() if line.strip()]

    selected: list[tuple[int, str]] = []
    for name in names:
        if not name.startswith(f"{prefix}-"):
            continue
        suffix = name.rsplit("-", 1)[-1]
        if not suffix.isdigit():
            continue
        index = int(suffix)
        if start <= index <= end:
            selected.append((index, name))
    selected.sort(key=lambda x: x[0])
    return selected


def compliance_for(policy_idx: int, cluster_index: int) -> str:
    zero_based = cluster_index - 1
    if zero_based in NONCOMPLIANT[policy_idx]:
        return "NonCompliant"
    if zero_based in PENDING[policy_idx]:
        return "Pending"
    return "Compliant"


def build_status_entries(clusters: list[tuple[int, str]], policy_idx: int) -> list[dict[str, str]]:
    entries = []
    for cluster_index, name in clusters:
        entries.append(
            {
                "clustername": name,
                "clusternamespace": name,
                "compliant": compliance_for(policy_idx, cluster_index),
            }
        )
    return entries


def patch_policy(policy_name: str, entries: list[dict[str, str]], dry_run: bool) -> None:
    for entry in entries:
        value = entry["compliant"]
        if value not in VALID_COMPLIANT:
            raise ValueError(f"invalid compliant value {value!r} for {entry['clustername']}")

    payload = {"status": {"status": entries}}
    cmd = [
        "patch",
        "policy",
        policy_name,
        "-n",
        GOVERNANCE_NS,
        "--subresource=status",
        "--type=merge",
        "-p",
        json.dumps(payload),
    ]
    if dry_run:
        print("oc", " ".join(cmd[:6]), "-p", f"<{len(entries)} entries>")
        return
    run_oc(cmd)
    print(f"Patched {policy_name}: {len(entries)} cluster status entries")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=750)
    parser.add_argument("--prefix", default="mock-sno")
    parser.add_argument("--width", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        clusters = list_repro_clusters(args.prefix, args.width, args.start, args.end)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not clusters:
        print("No repro clusters found in range — apply fleet first.", file=sys.stderr)
        return 1

    print(f"Patching policy status for {len(clusters)} clusters (index {args.start}-{args.end})")
    for idx, policy_name in enumerate(POLICY_NAMES):
        entries = build_status_entries(clusters, idx)
        patch_policy(policy_name, entries, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
