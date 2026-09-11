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
DEFAULT_POLICY_NAMES = ["perf-policy1", "perf-policy2", "perf-policy3"]
DEFAULT_GOVERNANCE_NS = "perf-governance"
FALLBACK_GOVERNANCE_NS = ("acm-43194-governance", "perf-governance")


def parse_repro_label() -> tuple[str, str]:
    label = os.environ.get("REPRO_LABEL", "acm-perf-repro=true")
    if "=" in label:
        key, value = label.split("=", 1)
        return key, value
    return label, "true"


def run_oc(args: list[str]) -> str:
    result = subprocess.run(["oc", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def namespace_exists(name: str) -> bool:
    try:
        run_oc(["get", "ns", name])
        return True
    except RuntimeError:
        return False


def list_policy_names_in_ns(governance_ns: str, *, repro_only: bool) -> list[str]:
    args = ["get", "policy.policy.open-cluster-management.io", "-n", governance_ns, "-o", "name"]
    if repro_only:
        key, value = parse_repro_label()
        args[2:2] = ["-l", f"{key}={value}"]
    try:
        out = run_oc(args)
    except RuntimeError:
        return []
    return sorted(line.split("/", 1)[-1] for line in out.splitlines() if line.strip())


def resolve_governance_ns(explicit: str | None) -> str:
    preferred = explicit or os.environ.get("GOVERNANCE_NS") or DEFAULT_GOVERNANCE_NS
    if namespace_exists(preferred) and list_policy_names_in_ns(preferred, repro_only=False):
        return preferred

    key, value = parse_repro_label()
    try:
        out = run_oc(
            [
                "get",
                "policy.policy.open-cluster-management.io",
                "-A",
                "-l",
                f"{key}={value}",
                "-o",
                "jsonpath={range .items[*]}{.metadata.namespace}{\\n}{end}",
            ]
        )
        namespaces = sorted({line.strip() for line in out.splitlines() if line.strip()})
        if len(namespaces) == 1:
            print(f"Auto-detected governance namespace: {namespaces[0]}", file=sys.stderr)
            return namespaces[0]
        if len(namespaces) > 1:
            raise RuntimeError(
                f"multiple governance namespaces with repro label: {namespaces}; set GOVERNANCE_NS"
            )
    except RuntimeError:
        pass

    for fallback in FALLBACK_GOVERNANCE_NS:
        if namespace_exists(fallback) and list_policy_names_in_ns(fallback, repro_only=False):
            print(f"Auto-detected governance namespace: {fallback}", file=sys.stderr)
            return fallback

    raise RuntimeError(
        f"namespace {preferred!r} not found and no Policy CRs on cluster.\n"
        "Run: ./apply-governance.sh"
    )


def resolve_policy_names(governance_ns: str, explicit: str | None) -> list[str]:
    if explicit:
        names = [part.strip() for part in explicit.split(",") if part.strip()]
        if names:
            return names

    env_names = os.environ.get("POLICY_NAMES", "")
    if env_names:
        names = [part.strip() for part in env_names.split(",") if part.strip()]
        if names:
            return names

    names = list_policy_names_in_ns(governance_ns, repro_only=True)
    if names:
        return names

    names = list_policy_names_in_ns(governance_ns, repro_only=False)
    if names:
        print(
            f"Using {len(names)} policies in {governance_ns} (no repro label on Policy CRs)",
            file=sys.stderr,
        )
        return names

    return DEFAULT_POLICY_NAMES.copy()


def list_repro_clusters(prefix: str, width: int, start: int, end: int) -> list[tuple[int, str]]:
    key, value = parse_repro_label()
    names: list[str] = []
    try:
        out = run_oc(["get", "managedcluster", "-l", f"{key}={value}", "-o", "name"])
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
    preset_idx = min(policy_idx, len(NONCOMPLIANT) - 1)
    zero_based = cluster_index - 1
    if zero_based in NONCOMPLIANT[preset_idx]:
        return "NonCompliant"
    if zero_based in PENDING[preset_idx]:
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


def patch_policy(
    policy_name: str,
    governance_ns: str,
    entries: list[dict[str, str]],
    dry_run: bool,
) -> None:
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
        governance_ns,
        "--subresource=status",
        "--type=merge",
        "-p",
        json.dumps(payload),
    ]
    if dry_run:
        print("oc", " ".join(cmd[:6]), "-p", f"<{len(entries)} entries>")
        return
    run_oc(cmd)
    print(f"Patched {policy_name} ({governance_ns}): {len(entries)} cluster status entries")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=750)
    parser.add_argument("--prefix", default=os.environ.get("CLUSTER_PREFIX", "mock-sno"))
    parser.add_argument("--width", type=int, default=int(os.environ.get("CLUSTER_WIDTH", "4")))
    parser.add_argument(
        "--governance-ns",
        default=os.environ.get("GOVERNANCE_NS", ""),
        help="Namespace with Policy CRs (auto-detect if missing)",
    )
    parser.add_argument(
        "--policy-names",
        default=os.environ.get("POLICY_NAMES", ""),
        help="Comma-separated policy names (auto-detect from governance ns if omitted)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        governance_ns = resolve_governance_ns(args.governance_ns or None)
        policy_names = resolve_policy_names(governance_ns, args.policy_names or None)
        clusters = list_repro_clusters(args.prefix, args.width, args.start, args.end)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not clusters:
        print("No repro clusters found in range — apply fleet first.", file=sys.stderr)
        return 1

    print(
        f"Patching policy status for {len(clusters)} clusters "
        f"(index {args.start}-{args.end}) in ns {governance_ns}"
    )
    print(f"Policies: {', '.join(policy_names)}")

    for idx, policy_name in enumerate(policy_names):
        entries = build_status_entries(clusters, idx)
        try:
            patch_policy(policy_name, governance_ns, entries, args.dry_run)
        except RuntimeError as exc:
            print(f"error patching {policy_name}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
