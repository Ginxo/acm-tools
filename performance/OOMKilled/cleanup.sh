#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo "Deleting labeled repro resources..."
oc delete agentmachine,agent,baremetalhost,nmstateconfig,agentclusterinstall,infraenv -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete machinepool,clusterprovision,clusterdeployment -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete policyreport -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete csr -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete managedclusteraddon -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete managedclusterinfo -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete managedcluster -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete policy.policy.open-cluster-management.io -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete secret -A -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete ns -l "${LABEL}=true" --wait=false 2>/dev/null || true
oc delete clusterimageset "${IMG}" --ignore-not-found 2>/dev/null || true

echo "Restoring controllers..."
bash "$(dirname "$0")/restore-controllers.sh" 2>/dev/null || true

echo "Removing 3Gi memory limit (optional)..."
oc -n "$MCE_NS" set resources deploy/console-mce-console --limits=memory- --requests=memory=40Mi 2>/dev/null || true

echo "Cleanup complete"
