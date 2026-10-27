#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Patch status subresources for console-cache layer.

Uses ``oc patch --patch-file`` so large Policy.status payloads do not hit ARG_MAX.

Policy.status is built from the fleet cluster list (schema-valid fields only:
clustername, clusternamespace, compliant). Other kinds take status from the
generated manifest when present.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Import shared status builder when available.
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
try:
    from generate_console_cache import policy_status_entries  # type: ignore
except ImportError:
    # Module is generate-console-cache.py (hyphen) — load via importlib.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_console_cache",
        SCRIPT_DIR / "generate-console-cache.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    policy_status_entries = mod.policy_status_entries


def run_oc(args: list[str]) -> str:
    result = subprocess.run(["oc", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "oc failed")
    return result.stdout


def load_docs(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        return [d for d in yaml.safe_load_all(text) if d]
    except ImportError:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        raise SystemExit("install pyyaml or generate with --json") from None


def load_clusters(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def list_console_policies(ns: str) -> list[str]:
    out = run_oc(
        [
            "get",
            "policy.policy.open-cluster-management.io",
            "-n",
            ns,
            "-l",
            "acm-perf-repro=true,acm-perf-layer=console-cache",
            "-o",
            "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}",
        ]
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def patch_status(api: str, name: str, ns: str | None, status: dict, *, dry_run: bool) -> None:
    payload = {"status": status}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(payload, fh)
        patch_path = fh.name
    try:
        cmd = [
            "patch",
            api,
            name,
            "--subresource=status",
            "--type=merge",
            f"--patch-file={patch_path}",
        ]
        if ns:
            cmd[2:2] = ["-n", ns]
        if dry_run:
            size = Path(patch_path).stat().st_size
            print(f"[dry-run] oc {' '.join(cmd[:6])}  ({size} bytes patch)")
            return
        run_oc(cmd)
        print(f"patched status {api}/{name}" + (f" -n {ns}" if ns else ""))
    finally:
        try:
            os.unlink(patch_path)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, nargs="?", default=None)
    parser.add_argument(
        "--clusters-file",
        type=Path,
        default=SCRIPT_DIR / "generated" / "console-cache-clusters.txt",
    )
    parser.add_argument("--governance-ns", default="perf-console-cache")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    clusters = load_clusters(args.clusters_file)
    patched = 0
    failed = 0

    # 1) Fat Policy status from live Policy list + fleet clusters
    if clusters:
        try:
            policies = list_console_policies(args.governance_ns)
        except RuntimeError as exc:
            print(f"warning: list policies: {exc}", file=sys.stderr)
            policies = []
        for idx, pname in enumerate(policies):
            status = {
                "compliant": "NonCompliant",
                "placement": [
                    {
                        "placement": "perf-fleet-placement",
                        "placementBinding": f"{pname}-binding",
                    }
                ],
                "status": policy_status_entries(clusters, idx),
            }
            try:
                patch_status(
                    "policy.policy.open-cluster-management.io",
                    pname,
                    args.governance_ns,
                    status,
                    dry_run=args.dry_run,
                )
                patched += 1
            except RuntimeError as exc:
                failed += 1
                print(f"warning: Policy/{pname}: {exc}", file=sys.stderr)
    else:
        print("warning: no clusters file — skipping Policy status density", file=sys.stderr)

    # 2) Other kinds: status embedded in manifest (PlacementDecision, Agent, …)
    if args.manifest and args.manifest.is_file():
        docs = load_docs(args.manifest)
        api_map = {
            "PlacementDecision": "placementdecision.cluster.open-cluster-management.io",
            "Agent": "agent.agent-install.openshift.io",
            "BareMetalHost": "baremetalhost.metal3.io",
            "HostedCluster": "hostedcluster.hypershift.openshift.io",
        }
        for doc in docs:
            kind = doc.get("kind")
            status = doc.get("status")
            meta = doc.get("metadata") or {}
            name = meta.get("name")
            ns = meta.get("namespace")
            if kind not in api_map or not status or not name:
                continue
            try:
                patch_status(api_map[kind], name, ns, status, dry_run=args.dry_run)
                patched += 1
            except RuntimeError as exc:
                failed += 1
                print(f"warning: {kind}/{name}: {exc}", file=sys.stderr)

    print(f"status patches ok={patched} failed={failed}")
    return 1 if failed and patched == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
