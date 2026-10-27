#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Console backend DefaultWatchSpecs mirror for acm-tools performance scripts.

KEEP IN SYNC with:
  github.com/stolostron/console/backend/internal/informers/specs.go
  DefaultWatchSpecs() — currently 69 specs.

When specs.go changes, update WATCH_SPECS below and bump EXPECTED_SPEC_COUNT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Must match TestDefaultWatchSpecsCount in console backend.
EXPECTED_SPEC_COUNT = 69

# Aggregate engine reads these from the informer cache — do not recommend moving off SSE.
AGGREGATE_CACHE_KINDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("cluster.open-cluster-management.io/v1", "ManagedCluster"),
        ("app.k8s.io/v1beta1", "Application"),
        ("argoproj.io/v1alpha1", "Application"),
        ("argoproj.io/v1alpha1", "ApplicationSet"),
        ("apps.open-cluster-management.io/v1", "Subscription"),
        ("cluster.open-cluster-management.io/v1beta1", "PlacementDecision"),
    }
)

# Used across most console pages via LoadData atoms — global snapshot until full migration.
GLOBAL_SNAPSHOT_KINDS: frozenset[str] = frozenset({"Namespace", "ManagedCluster"})

# Likely first movers to useFleetSearch once density is realistic.
FLEET_SEARCH_CANDIDATE_KINDS: frozenset[str] = frozenset(
    {
        "Policy",
        "PolicyReport",
        "DiscoveredCluster",
        "ManagedClusterInfo",
        "ManagedClusterAddOn",
        "ClusterDeployment",
        "HostedCluster",
        "MachinePool",
        "ClusterProvision",
        "ClusterClaim",
        "BareMetalHost",
        "Agent",
        "InfraEnv",
        "NodePool",
        "PolicySet",
        "PolicyAutomation",
        "PlacementBinding",
        "SubscriptionReport",
        "Channel",
        "HelmRelease",
        "GitOpsCluster",
    }
)


@dataclass(frozen=True)
class WatchSpec:
    """One hub list/watch definition from DefaultWatchSpecs()."""

    kind: str
    api_version: str
    cluster_scoped: bool = False
    polled: bool = False
    cache_only: bool = False
    labels: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def forward(self) -> bool:
        return (not self.cache_only) and (not self.polled)

    @property
    def mode(self) -> str:
        if self.cache_only:
            return "cache-only"
        if self.polled:
            return "polled"
        return "sse"

    @property
    def keep_managed_fields(self) -> bool:
        # transform.go strips managedFields for every kind except Policy.
        return self.kind == "Policy"

    def selector_query(self, which: str) -> str:
        m = self.labels if which == "label" else self.fields
        if not m:
            return ""
        return ",".join(f"{k}={v}" for k, v in sorted(m.items()))

    def label_selector(self) -> str:
        return self.selector_query("label")

    def field_selector(self) -> str:
        return self.selector_query("field")

    def short_selector(self) -> str:
        parts: list[str] = []
        if self.labels:
            parts.append("l:" + self.label_selector())
        if self.fields:
            parts.append("f:" + self.field_selector())
        return " ".join(parts) if parts else "-"

    def key(self) -> str:
        return f"{self.api_version}|{self.kind}|{self.label_selector()}|{self.field_selector()}"

    def migration_hint(self) -> str:
        """How to interpret this row for backend memory liberation."""
        pair = (self.api_version, self.kind)
        if pair in AGGREGATE_CACHE_KINDS:
            return "keep-aggregate"
        if self.cache_only:
            return "keep-cache-only"
        if self.kind in {"Secret", "ConfigMap", "CertificateSigningRequest", "Service", "ClusterRole"}:
            if self.labels or self.fields:
                return "keep-selector"
        if self.kind in GLOBAL_SNAPSHOT_KINDS:
            return "keep-global"
        if self.mode == "sse" and self.kind in FLEET_SEARCH_CANDIDATE_KINDS:
            return "candidate-fleet-search"
        if self.mode == "polled":
            return "polled-aggregate"
        return "review"


def _spec(
    kind: str,
    api_version: str,
    *,
    cluster: bool = False,
    polled: bool = False,
    cache: bool = False,
    labels: dict[str, str] | None = None,
    fields: dict[str, str] | None = None,
) -> WatchSpec:
    return WatchSpec(
        kind=kind,
        api_version=api_version,
        cluster_scoped=cluster,
        polled=polled,
        cache_only=cache,
        labels=dict(labels or {}),
        fields=dict(fields or {}),
    )


# Source of truth mirror — order matches specs.go DefaultWatchSpecs().
WATCH_SPECS: list[WatchSpec] = [
    _spec("ClusterManagementAddOn", "addon.open-cluster-management.io/v1alpha1", cluster=True),
    _spec("ManagedClusterAddOn", "addon.open-cluster-management.io/v1alpha1"),
    _spec("Agent", "agent-install.openshift.io/v1beta1"),
    _spec("AgentServiceConfig", "agent-install.openshift.io/v1beta1", cluster=True),
    _spec("InfraEnv", "agent-install.openshift.io/v1beta1"),
    _spec("NMStateConfig", "agent-install.openshift.io/v1beta1"),
    _spec("Application", "app.k8s.io/v1beta1"),
    _spec("Channel", "apps.open-cluster-management.io/v1"),
    _spec("GitOpsCluster", "apps.open-cluster-management.io/v1beta1"),
    _spec("HelmRelease", "apps.open-cluster-management.io/v1"),
    _spec("Subscription", "apps.open-cluster-management.io/v1"),
    _spec("SubscriptionReport", "apps.open-cluster-management.io/v1alpha1"),
    _spec("Application", "argoproj.io/v1alpha1", polled=True),
    _spec("ApplicationSet", "argoproj.io/v1alpha1", polled=True),
    _spec("ArgoCD", "argoproj.io/v1alpha1"),
    _spec("Authentication", "config.openshift.io/v1", cache=True, cluster=True),
    _spec("MultiClusterHub", "operator.open-cluster-management.io/v1", cache=True),
    _spec("Infrastructure", "config.openshift.io/v1", cluster=True),
    _spec(
        "CertificateSigningRequest",
        "certificates.k8s.io/v1",
        cluster=True,
        labels={"open-cluster-management.io/cluster-name": ""},
    ),
    _spec("ManagedCluster", "cluster.open-cluster-management.io/v1", cluster=True),
    _spec("Placement", "cluster.open-cluster-management.io/v1beta1"),
    _spec("PlacementDecision", "cluster.open-cluster-management.io/v1beta1"),
    _spec("ManagedClusterSetBinding", "cluster.open-cluster-management.io/v1beta2"),
    _spec("ManagedClusterSet", "cluster.open-cluster-management.io/v1beta2", cluster=True),
    _spec("ClusterCurator", "cluster.open-cluster-management.io/v1beta1"),
    _spec("Subscription", "operators.coreos.com/v1alpha1"),
    _spec("ClusterExtension", "olm.operatorframework.io/v1", cluster=True),
    _spec("DiscoveredCluster", "discovery.open-cluster-management.io/v1"),
    _spec("DiscoveryConfig", "discovery.open-cluster-management.io/v1"),
    _spec("AgentClusterInstall", "extensions.hive.openshift.io/v1beta1"),
    _spec("ClusterClaim", "hive.openshift.io/v1"),
    _spec("ClusterDeployment", "hive.openshift.io/v1"),
    _spec("ClusterImageSet", "hive.openshift.io/v1", cluster=True),
    _spec("ClusterPool", "hive.openshift.io/v1"),
    _spec("ClusterProvision", "hive.openshift.io/v1"),
    _spec("MachinePool", "hive.openshift.io/v1"),
    _spec("ManagedClusterInfo", "internal.open-cluster-management.io/v1beta1"),
    _spec("BareMetalHost", "metal3.io/v1alpha1"),
    _spec("MultiClusterEngine", "multicluster.openshift.io/v1", cluster=True),
    _spec("ClusterVersion", "config.openshift.io/v1", cluster=True),
    _spec("StorageClass", "storage.k8s.io/v1", cluster=True),
    _spec("PlacementBinding", "policy.open-cluster-management.io/v1"),
    _spec("Policy", "policy.open-cluster-management.io/v1"),
    _spec("PolicyAutomation", "policy.open-cluster-management.io/v1beta1"),
    _spec("PolicySet", "policy.open-cluster-management.io/v1beta1"),
    _spec("SubmarinerConfig", "submarineraddon.open-cluster-management.io/v1alpha1"),
    _spec("AnsibleJob", "tower.ansible.com/v1alpha1"),
    _spec("AnsibleWorkflow", "tower.ansible.com/v1alpha1"),
    _spec("ConfigMap", "v1", fields={"metadata.name": "assisted-service"}),
    _spec(
        "ConfigMap",
        "v1",
        fields={
            "metadata.namespace": "openshift-config-managed",
            "metadata.name": "console-public",
        },
    ),
    _spec("ConfigMap", "v1", fields={"metadata.name": "console-search-config"}),
    _spec("Namespace", "v1", cluster=True),
    _spec("Secret", "v1", labels={"cluster.open-cluster-management.io/credentials": ""}),
    _spec("Secret", "v1", labels={"cluster.open-cluster-management.io/type": "ans"}),
    _spec("Secret", "v1", fields={"metadata.name": "auto-import-secret"}),
    _spec("Secret", "v1", labels={"argocd.argoproj.io/secret-type": "repository"}),
    _spec("PolicyReport", "wgpolicyk8s.io/v1alpha2"),
    _spec(
        "ClusterRole",
        "rbac.authorization.k8s.io/v1",
        cluster=True,
        labels={"rbac.open-cluster-management.io/filter": "vm-clusterroles"},
    ),
    _spec("HostedCluster", "hypershift.openshift.io/v1beta1"),
    _spec("NodePool", "hypershift.openshift.io/v1beta1"),
    _spec("AgentMachine", "capi-provider.agent-install.openshift.io/v1alpha1"),
    _spec("ConfigMap", "v1", labels={"hypershift.openshift.io/supported-versions": "true"}),
    _spec("Search", "search.open-cluster-management.io/v1alpha1"),
    _spec(
        "ConfigMap",
        "v1",
        fields={"metadata.name": "grafana-dashboard-acm-openshift-virtualization-clusters-overview"},
    ),
    _spec(
        "ConfigMap",
        "v1",
        fields={"metadata.name": "grafana-dashboard-acm-openshift-virtualization-single-vm-view"},
    ),
    _spec("MulticlusterRoleAssignment", "rbac.open-cluster-management.io/v1beta1"),
    _spec("User", "user.openshift.io/v1", cluster=True),
    _spec("Group", "user.openshift.io/v1", cluster=True),
    _spec(
        "Service",
        "v1",
        fields={
            "metadata.name": "cluster-proxy-addon-user",
            "metadata.namespace": "multicluster-engine",
        },
    ),
]


def validate_specs() -> None:
    if len(WATCH_SPECS) != EXPECTED_SPEC_COUNT:
        raise AssertionError(
            f"WATCH_SPECS has {len(WATCH_SPECS)} entries, expected {EXPECTED_SPEC_COUNT}. "
            "Update watch_specs.py to match backend/internal/informers/specs.go"
        )


def specs_as_dicts() -> list[dict[str, Any]]:
    validate_specs()
    out: list[dict[str, Any]] = []
    for s in WATCH_SPECS:
        out.append(
            {
                "kind": s.kind,
                "apiVersion": s.api_version,
                "clusterScoped": s.cluster_scoped,
                "polled": s.polled,
                "cacheOnly": s.cache_only,
                "mode": s.mode,
                "labels": s.labels,
                "fields": s.fields,
                "labelSelector": s.label_selector(),
                "fieldSelector": s.field_selector(),
                "migrationHint": s.migration_hint(),
            }
        )
    return out


if __name__ == "__main__":
    validate_specs()
    print(f"OK: {len(WATCH_SPECS)} watch specs (expected {EXPECTED_SPEC_COUNT})")
    for s in WATCH_SPECS:
        print(f"  {s.mode:10} {s.api_version}/{s.kind} {s.short_selector()}")
