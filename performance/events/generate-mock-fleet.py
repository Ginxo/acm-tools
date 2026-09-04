#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Generate mock fleet YAML for ACM performance testing."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

ADDON_NAMES = [
    "application-manager",
    "cert-policy-controller",
    "cluster-proxy",
    "config-policy-controller",
    "governance-policy-framework",
    "hypershift-addon",
    "managed-serviceaccount",
    "work-manager",
]

FEATURE_LABELS = {
    "feature.open-cluster-management.io/addon-application-manager": "available",
    "feature.open-cluster-management.io/addon-cert-policy-controller": "available",
    "feature.open-cluster-management.io/addon-cluster-proxy": "available",
    "feature.open-cluster-management.io/addon-config-policy-controller": "available",
    "feature.open-cluster-management.io/addon-governance-policy-framework": "available",
    "feature.open-cluster-management.io/addon-hypershift-addon": "available",
    "feature.open-cluster-management.io/addon-managed-serviceaccount": "available",
    "feature.open-cluster-management.io/addon-work-manager": "available",
}


def cluster_name(prefix: str, width: int, index: int) -> str:
    return f"{prefix}-{index:0{width}d}"


def base_labels(name: str, cluster_set: str) -> dict[str, str]:
    labels = {
        "acm-perf-repro": "true",
        "cloud": "Amazon",
        "cluster.open-cluster-management.io/clusterset": cluster_set,
        "clusterID": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
        "name": name,
        "openshiftVersion": "4.22.12",
        "openshiftVersion-major": "4",
        "openshiftVersion-major-minor": "4.22",
        "velero.io/exclude-from-backup": "true",
        "vendor": "OpenShift",
    }
    labels.update(FEATURE_LABELS)
    return labels


def namespace(name: str, cluster_set: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": {
                "acm-perf-repro": "true",
                "cluster.open-cluster-management.io/clusterset": cluster_set,
                "open-cluster-management.io/cluster-name": name,
            },
        },
    }


def managed_cluster(name: str, cluster_set: str) -> dict[str, Any]:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1",
        "kind": "ManagedCluster",
        "metadata": {
            "name": name,
            "labels": base_labels(name, cluster_set),
            "annotations": {
                "installer.multicluster.openshift.io/release-version": "2.17.2",
                "open-cluster-management/created-via": "acm-perf-repro",
            },
        },
        "spec": {"hubAcceptsClient": True},
    }


def managed_cluster_info(name: str, cluster_set: str) -> dict[str, Any]:
    return {
        "apiVersion": "internal.open-cluster-management.io/v1beta1",
        "kind": "ManagedClusterInfo",
        "metadata": {
            "name": name,
            "namespace": name,
            "labels": base_labels(name, cluster_set),
        },
        "spec": {"masterEndpoint": f"https://api.{name}.example.com:6443"},
        "status": {
            "cloudVendor": "Amazon",
            "clusterID": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
            "distributionInfo": {
                "type": "OCP",
                "ocp": {
                    "version": "4.22.12",
                    "desiredVersion": "4.22.12",
                    "channel": "stable-4.22",
                },
            },
            "kubeVendor": "OpenShift",
            "nodeList": [
                {
                    "name": f"{name}-sno",
                    "capacity": {"cpu": "8", "memory": "32159900Ki", "socket": "1"},
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "labels": {
                        "node-role.kubernetes.io/control-plane": "",
                        "node-role.kubernetes.io/master": "",
                        "node-role.kubernetes.io/worker": "",
                        "node.openshift.io/os_id": "rhcos",
                    },
                }
            ],
        },
    }


def managed_cluster_addon(name: str, addon: str, cluster_set: str) -> dict[str, Any]:
    return {
        "apiVersion": "addon.open-cluster-management.io/v1alpha1",
        "kind": "ManagedClusterAddOn",
        "metadata": {
            "name": addon,
            "namespace": name,
            "labels": {
                "acm-perf-repro": "true",
                "cluster.open-cluster-management.io/clusterset": cluster_set,
            },
        },
        "spec": {"installNamespace": "open-cluster-management-agent-addon"},
        "status": {
            "conditions": [
                {
                    "type": "Available",
                    "status": "True",
                    "reason": "ManagedClusterAddOnLeaseUpdated",
                    "message": f"{addon} add-on is available (mock).",
                }
            ],
            "namespace": "open-cluster-management-agent-addon",
        },
    }


def emit_yaml_docs(docs: list[dict[str, Any]]) -> None:
    try:
        import yaml  # type: ignore
    except ImportError:
        # Fallback: JSON stream (oc apply accepts JSON)
        for doc in docs:
            print(json.dumps(doc))
            print("---")
        return

    for i, doc in enumerate(docs):
        if i:
            print("---")
        print(yaml.dump(doc, default_flow_style=False, sort_keys=False).rstrip())


def generate_cluster_docs(
    name: str,
    cluster_set: str,
    *,
    include_status: bool,
    skip_mc: bool,
    skip_mci: bool,
    skip_addons: bool,
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    if not skip_mc:
        docs.append(namespace(name, cluster_set))
        docs.append(managed_cluster(name, cluster_set))
    if not skip_mci:
        docs.append(managed_cluster_info(name, cluster_set))
    if not skip_addons:
        for addon in ADDON_NAMES:
            docs.append(managed_cluster_addon(name, addon, cluster_set))
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mock SNO fleet manifests")
    parser.add_argument("--start", type=int, default=1, help="First cluster index (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="Last cluster index (inclusive)")
    parser.add_argument("--prefix", default="mock-sno", help="Cluster name prefix")
    parser.add_argument("--width", type=int, default=4, help="Zero-pad width for index")
    parser.add_argument("--cluster-set", default="perf-fleet", help="ManagedClusterSet name")
    parser.add_argument(
        "--resource",
        choices=("fleet", "all", "managedcluster", "managedclusterinfo", "addons"),
        default="fleet",
        help="fleet=NS+MC+addons (default; MCI is created by OCM). all=includes MCI (may AlreadyExist)",
    )
    parser.add_argument(
        "--include-mci",
        action="store_true",
        help="Include ManagedClusterInfo manifests (same as --resource all)",
    )
    parser.add_argument(
        "--output",
        choices=("yaml", "json"),
        default="yaml",
        help="Output format (yaml requires PyYAML)",
    )
    args = parser.parse_args()

    if args.start < 1 or args.end < args.start:
        print("error: invalid range", file=sys.stderr)
        return 1

    if args.include_mci:
        args.resource = "all"

    skip_mc = args.resource not in ("fleet", "all", "managedcluster")
    skip_mci = args.resource not in ("all", "managedclusterinfo")
    skip_addons = args.resource not in ("fleet", "all", "addons")

    docs: list[dict[str, Any]] = []
    for index in range(args.start, args.end + 1):
        name = cluster_name(args.prefix, args.width, index)
        docs.extend(
            generate_cluster_docs(
                name,
                args.cluster_set,
                include_status=False,
                skip_mc=skip_mc,
                skip_mci=skip_mci,
                skip_addons=skip_addons,
            )
        )

    if args.output == "json":
        print(json.dumps(docs))
    else:
        emit_yaml_docs(docs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
