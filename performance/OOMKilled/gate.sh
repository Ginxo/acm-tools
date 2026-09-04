#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"

gate() {
  MC=$(oc get managedcluster -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  MCI=$(oc get managedclusterinfo -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  MCA=$(oc get managedclusteraddon -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  POL=$(oc get policy.policy.open-cluster-management.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  NS=$(oc get ns -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  CD=$(oc get clusterdeployments.hive.openshift.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  MP=$(oc get machinepools.hive.openshift.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  CP=$(oc get clusterprovisions.hive.openshift.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  SEC=$(oc get secret -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  IE=$(oc get infraenv -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  AG=$(oc get agents.agent-install.openshift.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  BMH=$(oc get baremetalhost -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  NMC=$(oc get nmstateconfig -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  ACI=$(oc get agentclusterinstalls.extensions.hive.openshift.io -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  AM=$(oc get agentmachine -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l || echo 0)
  PR=$(oc get policyreport -A -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l)
  CSR=$(oc get csr -l "${LABEL}=true" --no-headers 2>/dev/null | wc -l || echo 0)
  TOTAL=$((MC + MCI + MCA + POL + NS + CD + MP + CP + SEC + IE + AG + BMH + NMC + ACI + AM + PR + CSR))

  AGENT_TARGET=$((CLUSTERS * HOSTS_PER_CLUSTER))
  AM_TARGET=$AGENT_TARGET
  if ! oc api-resources 2>/dev/null | rg -q 'agentmachine'; then
    AM_TARGET=0
  fi

  echo "GATE_A: MC=$MC MCI=$MCI MCA=$MCA POL=$POL NS=$NS  → need ${CLUSTERS}/${CLUSTERS}/$((CLUSTERS * 8))/3/${CLUSTERS}"
  echo "GATE_B: CD=$CD MP=$MP CP=$CP SEC=$SEC           → need ${CLUSTERS}/$((CLUSTERS * 2))/${CLUSTERS}/≥$((CLUSTERS * 4))"
  echo "GATE_C: IE=$IE AG=$AG BMH=$BMH NMC=$NMC ACI=$ACI AM=$AM → need ${CLUSTERS}/${AGENT_TARGET}/${AGENT_TARGET}/${AGENT_TARGET}/${CLUSTERS}/${AM_TARGET}"
  echo "GATE_D: PR=$PR CSR=$CSR                          → need ${CLUSTERS}/${CLUSTERS}"
  echo "LABELED_TOTAL=$TOTAL (target ≥1329 or ≥1079 without AgentMachine)"

  PASS=true
  [[ "$MC" -eq "$CLUSTERS" && "$MCI" -eq "$CLUSTERS" && "$MCA" -eq $((CLUSTERS * 8)) && "$POL" -eq 3 && "$NS" -eq "$CLUSTERS" ]] || PASS=false
  [[ "$CD" -eq "$CLUSTERS" && "$MP" -eq $((CLUSTERS * 2)) && "$CP" -eq "$CLUSTERS" && "$SEC" -ge $((CLUSTERS * 4)) ]] || PASS=false
  [[ "$IE" -eq "$CLUSTERS" && "$AG" -eq "$AGENT_TARGET" && "$BMH" -eq "$AGENT_TARGET" && "$NMC" -eq "$AGENT_TARGET" && "$ACI" -eq "$CLUSTERS" ]] || PASS=false
  [[ "$AM" -eq "$AM_TARGET" ]] || PASS=false
  [[ "$PR" -eq "$CLUSTERS" && "$CSR" -ge "$CLUSTERS" ]] || PASS=false

  $PASS && echo "GATE: PASS" || echo "GATE: FAIL"
}

gate
if [[ "${1:-}" == "--retention" ]]; then
  echo "Waiting 120s for retention check..."
  sleep 120
  echo "--- retention ---"
  gate
fi
