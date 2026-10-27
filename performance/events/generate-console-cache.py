#!/usr/bin/env python3
# Copyright Contributors to the Open Cluster Management project
"""Generate dense console-cache layer YAML for backend informer memory pressure.

Creates objects that match DefaultWatchSpecs selectors (unlike layer B Secrets
with credentials=true or layer D CSRs with non-empty cluster-name).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any

REPRO_KEY = "acm-perf-repro"
REPRO_VALUE = "true"
CONSOLE_NS = "perf-console-cache"
PADDING = "x" * 2048  # ~2 KiB filler to inflate objects without huge status arrays


def labels_base(**extra: str) -> dict[str, str]:
    out = {REPRO_KEY: REPRO_VALUE, "acm-perf-layer": "console-cache"}
    out.update(extra)
    return out


def meta(name: str, namespace: str | None = None, **extra_labels: str) -> dict[str, Any]:
    m: dict[str, Any] = {
        "name": name,
        "labels": labels_base(**extra_labels),
        "annotations": {"open-cluster-management/created-via": "acm-perf-console-cache"},
    }
    if namespace:
        m["namespace"] = namespace
    return m


def ns_obj(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": labels_base(),
        },
    }


def policy_status_entries(clusters: list[str], policy_idx: int) -> list[dict[str, Any]]:
    """Fat Policy.status.status[] — only CRD-declared fields (no relatedObjects)."""
    entries: list[dict[str, Any]] = []
    for i, name in enumerate(clusters):
        compliant = "Compliant"
        if i % 17 == 3:
            compliant = "NonCompliant"
        elif i % 23 == 5:
            compliant = "Pending"
        # Keep schema-valid keys only (clustername/clusternamespace/compliant).
        # Density comes from one entry per fleet cluster × many Policies.
        _ = policy_idx  # retained for stable compliance pattern per policy
        entries.append(
            {
                "clustername": name,
                "clusternamespace": name,
                "compliant": compliant,
            }
        )
    return entries


def policy_status_doc(name: str, ns: str, idx: int, clusters: list[str]) -> dict[str, Any]:
    """Status-only document used by patch-console-cache-status.py (not SSA create)."""
    return {
        "apiVersion": "policy.open-cluster-management.io/v1",
        "kind": "Policy",
        "metadata": {"name": name, "namespace": ns},
        "status": {
            "compliant": "NonCompliant",
            "placement": [{"placement": "perf-fleet-placement", "placementBinding": f"{name}-binding"}],
            "status": policy_status_entries(clusters, idx),
        },
    }


def policy_doc(name: str, ns: str, idx: int, clusters: list[str]) -> dict[str, Any]:
    # Status is applied separately via patch — SSA rejects unknown status fields and
    # create-time status is usually stripped anyway.
    _ = clusters, idx
    return {
        "apiVersion": "policy.open-cluster-management.io/v1",
        "kind": "Policy",
        "metadata": meta(name, ns),
        "spec": {
            "disabled": False,
            "remediationAction": "inform",
            "policy-templates": [
                {
                    "objectDefinition": {
                        "apiVersion": "policy.open-cluster-management.io/v1",
                        "kind": "ConfigurationPolicy",
                        "metadata": {"name": f"{name}-cfg"},
                        "spec": {
                            "remediationAction": "inform",
                            "severity": "low",
                            "namespaceSelector": {"include": ["default"], "exclude": ["kube-*"]},
                            "object-templates": [
                                {
                                    "complianceType": "musthave",
                                    "objectDefinition": {
                                        "apiVersion": "v1",
                                        "kind": "ConfigMap",
                                        "metadata": {"name": f"perf-{name}", "namespace": "default"},
                                        "data": {"pad": PADDING},
                                    },
                                }
                            ],
                        },
                    }
                }
            ],
        },
    }


def placement_binding(name: str, ns: str, policy_name: str) -> dict[str, Any]:
    return {
        "apiVersion": "policy.open-cluster-management.io/v1",
        "kind": "PlacementBinding",
        "metadata": meta(name, ns),
        "placementRef": {
            "name": "perf-fleet-placement",
            "kind": "Placement",
            "apiGroup": "cluster.open-cluster-management.io",
        },
        "subjects": [
            {
                "name": policy_name,
                "kind": "Policy",
                "apiGroup": "policy.open-cluster-management.io",
            }
        ],
    }


def policy_set(name: str, ns: str, policy_names: list[str]) -> dict[str, Any]:
    return {
        "apiVersion": "policy.open-cluster-management.io/v1beta1",
        "kind": "PolicySet",
        "metadata": meta(name, ns),
        "spec": {
            "description": f"perf console-cache policy set {PADDING[:128]}",
            "policies": policy_names,
        },
    }


def policy_automation(name: str, ns: str, policy_name: str) -> dict[str, Any]:
    return {
        "apiVersion": "policy.open-cluster-management.io/v1beta1",
        "kind": "PolicyAutomation",
        "metadata": meta(name, ns),
        "spec": {
            "policyRef": policy_name,
            "mode": "disabled",
            "delayAfterRunSeconds": 0,
            "automationDef": {
                "name": f"{name}-job",
                "secret": "perf-ans-000",
                "type": "AnsibleJob",
                "extra_vars": {"pad": PADDING[:128]},
            },
        },
    }


def placement_decision(name: str, ns: str, clusters: list[str]) -> dict[str, Any]:
    decisions = [{"clusterName": c, "reason": ""} for c in clusters]
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "PlacementDecision",
        "metadata": {
            **meta(name, ns),
            "labels": {
                **labels_base(),
                "cluster.open-cluster-management.io/placement": "perf-fleet-placement",
            },
        },
        "status": {"decisions": decisions},
    }


def fat_policy_report(name: str, namespace: str, result_count: int) -> dict[str, Any]:
    results = []
    for i in range(result_count):
        results.append(
            {
                "policy": f"perf-console-policy-{i % 20}",
                "rule": f"rule-{i}",
                "result": ["pass", "fail", "warn"][i % 3],
                "severity": ["low", "medium", "high"][i % 3],
                "message": f"fat policy report result {i} {PADDING[:256]}",
                "category": "configuration",
                "scored": True,
            }
        )
    return {
        "apiVersion": "wgpolicyk8s.io/v1alpha2",
        "kind": "PolicyReport",
        "metadata": meta(name, namespace),
        "results": results,
        "summary": {
            "pass": result_count // 3,
            "fail": result_count // 3,
            "warn": result_count - 2 * (result_count // 3),
        },
    }


def channel(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps.open-cluster-management.io/v1",
        "kind": "Channel",
        "metadata": meta(name, ns),
        "spec": {
            "type": "Git",
            "pathname": f"https://github.com/example/perf-{name}.git",
        },
    }


def subscription(name: str, ns: str, channel_name: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps.open-cluster-management.io/v1",
        "kind": "Subscription",
        "metadata": meta(name, ns),
        "spec": {
            "channel": f"{ns}/{channel_name}",
            "placement": {"local": True},
        },
    }


def application(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "app.k8s.io/v1beta1",
        "kind": "Application",
        "metadata": meta(name, ns),
        "spec": {
            "componentKinds": [{"group": "apps.open-cluster-management.io", "kind": "Subscription"}],
            "descriptor": {"type": "perf", "version": "1.0", "description": PADDING[:512]},
            "selector": {"matchLabels": {"app": name}},
        },
    }


def helm_release(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps.open-cluster-management.io/v1",
        "kind": "HelmRelease",
        "metadata": meta(name, ns),
        "spec": {
            "chart": {"repository": "https://charts.example.com", "name": name, "version": "1.0.0"},
            "values": {"pad": PADDING},
        },
    }


def subscription_report(name: str, ns: str, clusters: list[str]) -> dict[str, Any]:
    # SubscriptionReportResult schema: source + result (no "cluster" field).
    n = min(50, max(1, len(clusters)))
    results = [{"source": f"sub-{i}", "result": "deployed"} for i in range(n)]
    return {
        "apiVersion": "apps.open-cluster-management.io/v1alpha1",
        "kind": "SubscriptionReport",
        "metadata": meta(name, ns),
        "reportType": "Application",
        "results": results,
        "summary": {
            "deployed": str(len(results)),
            "inProgress": "0",
            "failed": "0",
            "clusters": str(len(results)),
        },
    }


def discovered_cluster(name: str, ns: str) -> dict[str, Any]:
    # Keep only commonly declared DiscoveredClusterSpec fields (no .spec.cloud).
    return {
        "apiVersion": "discovery.open-cluster-management.io/v1",
        "kind": "DiscoveredCluster",
        "metadata": {
            **meta(name, ns),
            "annotations": {
                "open-cluster-management/created-via": "acm-perf-console-cache",
                "acm-perf-pad": PADDING[:512],
            },
        },
        "spec": {
            "name": name,
            "displayName": f"{name} {PADDING[:256]}",
            "openshiftVersion": "4.22.12",
            "type": "ROSA",
            "apiUrl": f"https://api.{name}.perf.example.com:6443",
            "credential": {"name": "perf-discovery-creds", "namespace": ns},
            "isManagedCluster": False,
        },
    }


def discovery_config(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "discovery.open-cluster-management.io/v1",
        "kind": "DiscoveryConfig",
        "metadata": meta(name, ns),
        "spec": {
            "credential": "perf-discovery-creds",
            "filters": {"lastActive": 7, "openShiftVersions": ["4.22"]},
        },
    }


def hosted_cluster(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "hypershift.openshift.io/v1beta1",
        "kind": "HostedCluster",
        "metadata": {
            **meta(name, ns),
            "annotations": {
                "open-cluster-management/created-via": "acm-perf-console-cache",
                "acm-perf-pad": PADDING,
            },
        },
        "spec": {
            "release": {"image": "quay.io/openshift-release-dev/ocp-release:4.22.12-x86_64"},
            "networking": {
                "networkType": "OVNKubernetes",
                "clusterNetwork": [{"cidr": "10.132.0.0/14"}],
                "serviceNetwork": [{"cidr": "172.31.0.0/16"}],
            },
            "platform": {"type": "None"},
            "infraID": f"{name}-infra",
            "clusterID": str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
            "services": [
                {"service": "APIServer", "servicePublishingStrategy": {"type": "LoadBalancer"}},
                {"service": "Ignition", "servicePublishingStrategy": {"type": "Route"}},
                {"service": "Konnectivity", "servicePublishingStrategy": {"type": "Route"}},
                {"service": "OAuthServer", "servicePublishingStrategy": {"type": "Route"}},
            ],
            "pullSecret": {"name": f"{name}-pull"},
            "sshKey": {"name": f"{name}-ssh"},
        },
    }


def node_pool(name: str, ns: str, hc_name: str) -> dict[str, Any]:
    return {
        "apiVersion": "hypershift.openshift.io/v1beta1",
        "kind": "NodePool",
        "metadata": {
            **meta(name, ns),
            "annotations": {
                "open-cluster-management/created-via": "acm-perf-console-cache",
                "acm-perf-pad": PADDING[:512],
            },
        },
        "spec": {
            "clusterName": hc_name,
            "replicas": 2,
            "management": {"upgradeType": "Replace"},
            "platform": {"type": "None"},
            "release": {"image": "quay.io/openshift-release-dev/ocp-release:4.22.12-x86_64"},
        },
    }


def infra_env(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "agent-install.openshift.io/v1beta1",
        "kind": "InfraEnv",
        "metadata": meta(name, ns),
        "spec": {
            "clusterRef": {"name": name, "namespace": ns},
            "pullSecretRef": {"name": f"{name}-pull"},
            "sshAuthorizedKey": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC perf-stub",
            "agentLabels": {"pad": PADDING[:128]},
        },
    }


def agent(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "agent-install.openshift.io/v1beta1",
        "kind": "Agent",
        "metadata": meta(name, ns),
        "spec": {
            "clusterDeploymentName": {"name": name, "namespace": ns},
            "role": "master",
            "hostname": f"{name}.perf.example.com",
            "installation_disk_id": "/dev/sda",
            "approved": True,
        },
        "status": {
            "inventory": {
                "hostname": f"{name}.perf.example.com",
                "cpu": {"count": 8, "architecture": "x86_64"},
                "memory": {"physicalBytes": 34359738368},
                "disks": [
                    {
                        "id": f"{name}-sda",
                        "path": "/dev/sda",
                        "sizeBytes": 120000000000,
                        "driveType": "HDD",
                        "name": "sda",
                    }
                ],
                "interfaces": [
                    {
                        "name": "eth0",
                        "macAddress": "00:11:22:33:44:55",
                        "flags": ["up"],
                        "ipV4Addresses": ["192.0.2.10/24"],
                        "ipV6Addresses": ["2001:db8::10/64"],
                    }
                ],
            },
            "conditions": [{"type": "Ready", "status": "True", "message": PADDING[:128]}],
        },
    }


def bare_metal_host(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "metal3.io/v1alpha1",
        "kind": "BareMetalHost",
        "metadata": meta(name, ns),
        "spec": {
            "online": True,
            "bootMACAddress": "00:11:22:33:44:55",
            "bmc": {
                "address": f"redfish-virtualmedia://{name}.bmc.example.com/redfish/v1/Systems/1",
                "credentialsName": f"{name}-bmc",
            },
            "externallyProvisioned": False,
            "consumerRef": {"name": name, "namespace": ns},
            "image": {"url": f"http://example.com/{name}.qcow2", "checksum": "sha256:deadbeef"},
            "userData": {"name": f"{name}-userdata", "namespace": ns},
            "metaData": {"name": f"{name}-metadata", "namespace": ns},
            "networkData": {"name": f"{name}-network", "namespace": ns},
        },
        "status": {
            "operationalStatus": "OK",
            "hardwareProfile": "unknown",
            "poweredOn": True,
            "errorMessage": "",
            "errorCount": 0,
            "provisioning": {"state": "provisioned", "ID": name, "image": {"url": "http://example.com/img"}},
            "hardware": {
                "cpu": {"arch": "x86_64", "model": "perf", "clockMegahertz": 2500, "count": 8},
                "firmware": {"bios": {"vendor": "perf", "version": "1.0"}},
                "hostname": name,
                "nics": [{"name": "eth0", "mac": "00:11:22:33:44:55", "ip": "10.0.0.1"}],
                "storage": [{"name": "sda", "sizeBytes": 120000000000, "type": "HDD"}],
                "systemVendor": {"manufacturer": "perf", "productName": PADDING[:128]},
            },
        },
    }


def credential_secret(name: str, ns: str) -> dict[str, Any]:
    """Matches Secret watch: label credentials with empty value."""
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "cluster.open-cluster-management.io/credentials": "",
            },
        },
        "type": "Opaque",
        "data": {"stub": "e30=", "pad": __import__("base64").b64encode(PADDING.encode()).decode()},
    }


def ans_secret(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "cluster.open-cluster-management.io/type": "ans",
            },
        },
        "type": "Opaque",
        "data": {"host": "aHR0cHM6Ly9hYXAuZXhhbXBsZS5jb20=", "token": "e30="},
    }


def auto_import_secret(ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": "auto-import-secret",
            "namespace": ns,
            "labels": {REPRO_KEY: REPRO_VALUE, "acm-perf-layer": "console-cache"},
        },
        "type": "Opaque",
        "data": {"autoImportRetry": "NQo=", "kubeconfig": "e30="},
    }


def argo_repo_secret(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "argocd.argoproj.io/secret-type": "repository",
            },
        },
        "type": "Opaque",
        "stringData": {"type": "git", "url": f"https://github.com/example/{name}.git", "name": name},
    }


def csr_matching_watch(name: str, request_b64: str) -> dict[str, Any]:
    """Matches CSR watch: label cluster-name with empty value."""
    return {
        "apiVersion": "certificates.k8s.io/v1",
        "kind": "CertificateSigningRequest",
        "metadata": {
            "name": name,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "open-cluster-management.io/cluster-name": "",
            },
        },
        "spec": {
            "request": request_b64,
            "signerName": "kubernetes.io/kube-apiserver-client",
            "usages": ["client auth"],
        },
    }


# ── Dense kinds previously missing from console-cache ───────────────────────


def argo_application(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": meta(name, ns),
        "spec": {
            "project": "default",
            "source": {
                "repoURL": f"https://github.com/example/{name}.git",
                "path": "manifests",
                "targetRevision": "HEAD",
            },
            "destination": {"server": "https://kubernetes.default.svc", "namespace": ns},
            "syncPolicy": {"automated": {"prune": True}},
        },
        "status": {
            "health": {"status": "Healthy", "message": PADDING[:128]},
            "sync": {"status": "Synced", "revision": "abc123"},
            "resources": [
                {"group": "", "version": "v1", "kind": "ConfigMap", "name": f"{name}-cm", "status": "Synced"}
                for _ in range(5)
            ],
        },
    }


def argo_application_set(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "ApplicationSet",
        "metadata": meta(name, ns),
        "spec": {
            "generators": [{"list": {"elements": [{"cluster": "local", "url": "https://kubernetes.default.svc"}]}}],
            "template": {
                "metadata": {"name": f"{name}-{{{{cluster}}}}"},
                "spec": {
                    "project": "default",
                    "source": {"repoURL": "https://github.com/example/appset.git", "path": "apps"},
                    "destination": {"server": "{{url}}", "namespace": ns},
                },
            },
        },
    }


def argo_cd(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "ArgoCD",
        "metadata": meta(name, ns),
        "spec": {
            "server": {"route": {"enabled": True}},
            "controller": {"processors": {"operation": 10, "status": 10}},
        },
    }


def gitops_cluster(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "apps.open-cluster-management.io/v1beta1",
        "kind": "GitOpsCluster",
        "metadata": meta(name, ns),
        "spec": {
            "argoServer": {"argoNamespace": ns},
            "placementRef": {
                "apiVersion": "cluster.open-cluster-management.io/v1beta1",
                "kind": "Placement",
                "name": "perf-fleet-placement",
            },
        },
    }


def cluster_pool(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "hive.openshift.io/v1",
        "kind": "ClusterPool",
        "metadata": meta(name, ns),
        "spec": {
            "baseDomain": "perf.example.com",
            "imageSetRef": {"name": "perf-img-stub"},
            "platform": {"aws": {"credentialsSecretRef": {"name": f"{name}-aws"}, "region": "us-east-1"}},
            "pullSecretRef": {"name": f"{name}-pull"},
            "size": 1,
            "runningCount": 0,
        },
    }


def cluster_claim(name: str, ns: str, pool: str) -> dict[str, Any]:
    return {
        "apiVersion": "hive.openshift.io/v1",
        "kind": "ClusterClaim",
        "metadata": meta(name, ns),
        "spec": {"clusterPoolName": pool, "lifetime": "8h"},
    }


def agent_cluster_install(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "extensions.hive.openshift.io/v1beta1",
        "kind": "AgentClusterInstall",
        "metadata": meta(name, ns),
        "spec": {
            "clusterDeploymentRef": {"name": name},
            "imageSetRef": {"name": "perf-img-stub"},
            "networking": {
                "clusterNetwork": [{"cidr": "10.128.0.0/14", "hostPrefix": 23}],
                "serviceNetwork": ["172.30.0.0/16"],
            },
            "provisionRequirements": {"controlPlaneAgents": 1},
            "sshPublicKey": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC perf-stub",
        },
    }


def cluster_curator(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "cluster.open-cluster-management.io/v1beta1",
        "kind": "ClusterCurator",
        "metadata": meta(name, ns),
        "spec": {
            "desiredCuration": "install",
            "install": {
                "towerAuthSecret": f"{name}-ans",
                "prehook": [{"name": "pre-job", "extra_vars": {"pad": PADDING[:128]}}],
                "posthook": [{"name": "post-job"}],
            },
        },
    }


def nmstate_config(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "agent-install.openshift.io/v1beta1",
        "kind": "NMStateConfig",
        "metadata": meta(name, ns),
        "spec": {
            "config": {
                "interfaces": [
                    {
                        "name": "eth0",
                        "type": "ethernet",
                        "state": "up",
                        "ipv4": {"enabled": True, "address": [{"ip": "192.0.2.10", "prefix-length": 24}]},
                    }
                ]
            },
            "interfaces": [{"name": "eth0", "macAddress": "00:11:22:33:44:55"}],
        },
    }


def ansible_job(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "tower.ansible.com/v1alpha1",
        "kind": "AnsibleJob",
        "metadata": meta(name, ns),
        "spec": {
            "job_template_name": "perf-template",
            "tower_auth_secret": f"{name}-ans",
            "extra_vars": {"cluster_name": name, "pad": PADDING[:256]},
        },
    }


def ansible_workflow(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "tower.ansible.com/v1alpha1",
        "kind": "AnsibleWorkflow",
        "metadata": meta(name, ns),
        "spec": {
            "workflow_template_name": "perf-workflow",
            "connection_secret": f"{name}-ans",
            "extra_vars": {"pad": PADDING[:256]},
        },
    }


def submariner_config(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "submarineraddon.open-cluster-management.io/v1alpha1",
        "kind": "SubmarinerConfig",
        "metadata": meta(name, ns),
        "spec": {
            "IPSecNATTPort": 4500,
            "cableDriver": "libreswan",
            "credentialsSecret": {"name": f"{name}-creds"},
            "gatewayConfig": {"aws": {"instanceType": "m5.xlarge"}},
        },
    }


def multicluster_role_assignment(name: str, ns: str, user: str) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.open-cluster-management.io/v1beta1",
        "kind": "MulticlusterRoleAssignment",
        "metadata": meta(name, ns),
        "spec": {
            "subject": {"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": user},
            "roleAssignments": [
                {
                    "name": f"{name}-ra",
                    "clusterRole": "view",
                    "targetNamespaces": ["default", "kube-system"],
                    "clusterSelection": {
                        "type": "placements",
                        "placements": [{"name": "perf-fleet-placement", "namespace": "perf-governance"}],
                    },
                }
            ],
        },
    }


def vm_cluster_role(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": {
            "name": name,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "rbac.open-cluster-management.io/filter": "vm-clusterroles",
            },
        },
        "rules": [
            {"apiGroups": ["kubevirt.io"], "resources": ["virtualmachines"], "verbs": ["get", "list", "watch"]},
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"], "resourceNames": [PADDING[:32]]},
        ],
    }


def agent_machine(name: str, ns: str) -> dict[str, Any]:
    # AgentMachineSpec is typically just providerID (no agentRef in CRD schema).
    return {
        "apiVersion": "capi-provider.agent-install.openshift.io/v1alpha1",
        "kind": "AgentMachine",
        "metadata": meta(name, ns),
        "spec": {
            "providerID": f"agent://{name}",
        },
    }


def olm_subscription(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "Subscription",
        "metadata": meta(name, ns),
        "spec": {
            "channel": "stable",
            "name": name,
            "source": "redhat-operators",
            "sourceNamespace": "openshift-marketplace",
        },
    }


def openshift_user(name: str) -> dict[str, Any]:
    return {
        "apiVersion": "user.openshift.io/v1",
        "kind": "User",
        "metadata": {
            "name": name,
            "labels": labels_base(),
            "annotations": {"open-cluster-management/created-via": "acm-perf-console-cache", "acm-perf-pad": PADDING[:128]},
        },
        "identities": [f"perf:{name}"],
        "fullName": f"Perf User {name}",
    }


def openshift_group(name: str, users: list[str]) -> dict[str, Any]:
    return {
        "apiVersion": "user.openshift.io/v1",
        "kind": "Group",
        "metadata": {
            "name": name,
            "labels": labels_base(),
            "annotations": {"open-cluster-management/created-via": "acm-perf-console-cache"},
        },
        "users": users,
    }


def hypershift_versions_configmap(name: str, ns: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {
                REPRO_KEY: REPRO_VALUE,
                "acm-perf-layer": "console-cache",
                "hypershift.openshift.io/supported-versions": "true",
            },
        },
        "data": {"supported-versions": json.dumps({"versions": ["4.20", "4.21", "4.22"], "pad": PADDING[:256]})},
    }


def emit_yaml(docs: list[dict[str, Any]], stream=sys.stdout) -> None:
    try:
        import yaml  # type: ignore

        for i, doc in enumerate(docs):
            if i:
                stream.write("---\n")
            yaml.safe_dump(doc, stream, default_flow_style=False, sort_keys=False)
    except ImportError:
        for i, doc in enumerate(docs):
            if i:
                stream.write("---\n")
            stream.write(json.dumps(doc, indent=2))
            stream.write("\n")


def build_docs(
    *,
    policy_count: int,
    app_count: int,
    argo_count: int,
    discovery_count: int,
    hosted_count: int,
    assisted_count: int,
    hive_extra_count: int,
    ansible_count: int,
    submariner_count: int,
    mra_count: int,
    vm_role_count: int,
    olm_count: int,
    user_count: int,
    secret_count: int,
    csr_count: int,
    report_fat_count: int,
    report_results: int,
    clusters: list[str],
    include_csr_request: str,
) -> list[dict[str, Any]]:
    """Emit objects for every dense DefaultWatchSpecs kind (skip hub singletons)."""
    docs: list[dict[str, Any]] = [ns_obj(CONSOLE_NS)]
    # Use perf-console-cache for MRA too (informer is cluster-wide list; avoids
    # mutating the real open-cluster-management-global-set namespace).
    mra_ns = CONSOLE_NS
    policy_names: list[str] = []

    for i in range(policy_count):
        pname = f"perf-console-policy-{i:03d}"
        policy_names.append(pname)
        docs.append(policy_doc(pname, CONSOLE_NS, i, clusters))
        docs.append(placement_binding(f"{pname}-binding", CONSOLE_NS, pname))

    if policy_names:
        chunk = 10
        for i in range(0, len(policy_names), chunk):
            group = policy_names[i : i + chunk]
            docs.append(policy_set(f"perf-console-policyset-{i // chunk:02d}", CONSOLE_NS, group))
            docs.append(
                policy_automation(
                    f"perf-console-policyauto-{i // chunk:02d}",
                    CONSOLE_NS,
                    group[0],
                )
            )

    # Fat PlacementDecision covering the fleet (status-only; may need apply+patch).
    if clusters:
        docs.append(placement_decision("perf-console-placement-decision", CONSOLE_NS, clusters))
        # Extra decisions to densify PlacementDecision informer beyond one object.
        for i in range(min(10, max(1, len(clusters) // 100))):
            chunk = clusters[i * 50 : (i + 1) * 50] or clusters[:10]
            docs.append(placement_decision(f"perf-console-pd-extra-{i:02d}", CONSOLE_NS, chunk))

    for i in range(report_fat_count):
        ns = clusters[i % len(clusters)] if clusters else CONSOLE_NS
        docs.append(fat_policy_report(f"perf-console-pr-{i:03d}", ns, report_results))

    for i in range(app_count):
        ch = f"perf-channel-{i:03d}"
        sub = f"perf-sub-{i:03d}"
        docs.append(channel(ch, CONSOLE_NS))
        docs.append(subscription(sub, CONSOLE_NS, ch))
        docs.append(application(f"perf-app-{i:03d}", CONSOLE_NS))
        docs.append(helm_release(f"perf-helm-{i:03d}", CONSOLE_NS))
        docs.append(subscription_report(f"perf-subreport-{i:03d}", CONSOLE_NS, clusters))

    # ArgoCD + GitOps + Argo Applications / ApplicationSets (polled but still in cache).
    for i in range(max(1, min(5, argo_count // 10 or 1))):
        docs.append(argo_cd(f"perf-argocd-{i:02d}", CONSOLE_NS))
        docs.append(gitops_cluster(f"perf-gitops-{i:02d}", CONSOLE_NS))
    for i in range(argo_count):
        docs.append(argo_application(f"perf-argo-app-{i:03d}", CONSOLE_NS))
        docs.append(argo_application_set(f"perf-argo-appset-{i:03d}", CONSOLE_NS))

    docs.append(discovery_config("perf-discovery", CONSOLE_NS))
    for i in range(discovery_count):
        docs.append(discovered_cluster(f"perf-discovered-{i:03d}", CONSOLE_NS))

    for i in range(hosted_count):
        hc = f"perf-hosted-{i:03d}"
        docs.append(hosted_cluster(hc, CONSOLE_NS))
        docs.append(node_pool(f"{hc}-workers", CONSOLE_NS, hc))

    for i in range(assisted_count):
        an = f"perf-agent-{i:03d}"
        docs.append(infra_env(an, CONSOLE_NS))
        docs.append(agent(an, CONSOLE_NS))
        docs.append(bare_metal_host(f"perf-bmh-{i:03d}", CONSOLE_NS))
        docs.append(nmstate_config(f"perf-nmstate-{i:03d}", CONSOLE_NS))
        docs.append(agent_machine(f"perf-agentmachine-{i:03d}", CONSOLE_NS))

    # Hive extras not covered by layer B
    for i in range(hive_extra_count):
        pool = f"perf-pool-{i:03d}"
        docs.append(cluster_pool(pool, CONSOLE_NS))
        docs.append(cluster_claim(f"perf-claim-{i:03d}", CONSOLE_NS, pool))
        docs.append(agent_cluster_install(f"perf-aci-{i:03d}", CONSOLE_NS))
        docs.append(cluster_curator(f"perf-curator-{i:03d}", CONSOLE_NS))

    for i in range(ansible_count):
        docs.append(ansible_job(f"perf-ansible-job-{i:03d}", CONSOLE_NS))
        docs.append(ansible_workflow(f"perf-ansible-wf-{i:03d}", CONSOLE_NS))

    for i in range(submariner_count):
        # Prefer fleet namespaces when available (addon lives per managed cluster ns).
        ns = clusters[i % len(clusters)] if clusters else CONSOLE_NS
        docs.append(submariner_config(f"perf-submariner-{i:03d}", ns))

    for i in range(mra_count):
        docs.append(
            multicluster_role_assignment(
                f"perf-mra-{i:03d}",
                mra_ns,
                f"perf-user-{i:03d}",
            )
        )

    for i in range(vm_role_count):
        docs.append(vm_cluster_role(f"perf-vm-clusterrole-{i:03d}"))

    for i in range(olm_count):
        docs.append(olm_subscription(f"perf-olm-sub-{i:03d}", CONSOLE_NS))

    user_names: list[str] = []
    for i in range(user_count):
        uname = f"perf-user-{i:03d}"
        user_names.append(uname)
        docs.append(openshift_user(uname))
    for i in range(max(1, user_count // 10)):
        chunk = user_names[i * 10 : (i + 1) * 10] or user_names[:1]
        docs.append(openshift_group(f"perf-group-{i:02d}", chunk))

    docs.append(hypershift_versions_configmap("perf-hypershift-versions", CONSOLE_NS))

    for i in range(secret_count):
        docs.append(credential_secret(f"perf-creds-{i:03d}", CONSOLE_NS))
        docs.append(ans_secret(f"perf-ans-{i:03d}", CONSOLE_NS))
        docs.append(argo_repo_secret(f"perf-argo-repo-{i:03d}", CONSOLE_NS))
    if secret_count > 0:
        docs.append(auto_import_secret(CONSOLE_NS))

    for i in range(csr_count):
        docs.append(csr_matching_watch(f"perf-console-csr-{i:03d}", include_csr_request))

    return docs


def list_clusters_from_env(prefix: str, width: int, start: int, end: int) -> list[str]:
    """Synthetic names — apply script may replace with live fleet list."""
    return [f"{prefix}-{i:0{width}d}" for i in range(start, end + 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset",
        choices=("smoke", "target", "full"),
        default="smoke",
        help="smoke=small; target=dense fleet companions; full=all dense kinds at scale",
    )
    parser.add_argument("--prefix", default=os.environ.get("CLUSTER_PREFIX", "mock-sno"))
    parser.add_argument("--width", type=int, default=int(os.environ.get("CLUSTER_WIDTH", "4")))
    parser.add_argument("--cluster-start", type=int, default=1)
    parser.add_argument("--cluster-end", type=int, default=0, help="0 = derive from preset")
    parser.add_argument(
        "--clusters-file",
        default="",
        help="File with one ManagedCluster name per line (overrides synthetic range)",
    )
    parser.add_argument(
        "--csr-request",
        default="",
        help="Base64 CSR PEM; required for CSR docs (apply script generates if empty)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON list instead of YAML")
    args = parser.parse_args()

    # Counts for every dense watch kind. Hub singletons (MCH, Authentication, fixed
    # ConfigMaps/Services) are intentionally omitted — they are not density drivers.
    if args.preset == "smoke":
        counts = dict(
            policy_count=5,
            app_count=5,
            argo_count=5,
            discovery_count=10,
            hosted_count=2,
            assisted_count=2,
            hive_extra_count=3,
            ansible_count=3,
            submariner_count=3,
            mra_count=5,
            vm_role_count=5,
            olm_count=5,
            user_count=10,
            secret_count=5,
            csr_count=3,
            report_fat_count=5,
            report_results=20,
        )
        default_end = 10
    elif args.preset == "target":
        counts = dict(
            policy_count=40,
            app_count=100,
            argo_count=50,
            discovery_count=100,
            hosted_count=20,
            assisted_count=25,
            hive_extra_count=50,
            ansible_count=50,
            submariner_count=50,
            mra_count=50,
            vm_role_count=30,
            olm_count=40,
            user_count=100,
            secret_count=50,
            csr_count=20,
            report_fat_count=50,
            report_results=40,
        )
        default_end = 750
    else:  # full — max density for ranking all movable kinds
        counts = dict(
            policy_count=60,
            app_count=150,
            argo_count=150,
            discovery_count=200,
            hosted_count=50,
            assisted_count=50,
            hive_extra_count=100,
            ansible_count=100,
            submariner_count=100,
            mra_count=100,
            vm_role_count=50,
            olm_count=80,
            user_count=200,
            secret_count=100,
            csr_count=50,
            report_fat_count=100,
            report_results=60,
        )
        default_end = 750

    cluster_end = args.cluster_end or default_end
    if args.clusters_file:
        with open(args.clusters_file, encoding="utf-8") as fh:
            clusters = [line.strip() for line in fh if line.strip()]
    else:
        clusters = list_clusters_from_env(args.prefix, args.width, args.cluster_start, cluster_end)

    csr_req = args.csr_request or (
        # Minimal placeholder; apply-layer-console.sh regenerates real CSRs via openssl.
        "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURSBSRVFVRVNUCi0tLS0tRU5EIENFUlRJRklDQVRFIFJFUVVFU1QtLS0tLQ=="
    )

    docs = build_docs(
        clusters=clusters,
        include_csr_request=csr_req,
        **counts,
    )

    if args.json:
        json.dump(docs, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        emit_yaml(docs)
    print(f"# generated {len(docs)} docs for preset={args.preset} clusters={len(clusters)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
