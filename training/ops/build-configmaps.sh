#!/usr/bin/env bash
# Rebuild the code configMaps the jobs mount at /code, from the repo sources.
# The job pods run stock python:3.12 and pip-install deps at start; all project
# code arrives through these configMaps. Run after editing anything under train/ or quant/.
set -euo pipefail
CTX=example-cluster
NS=distill
ROOT="$(cd "$(dirname "$0")/.." && pwd)"   # -> training/
K="kubectl --context $CTX -n $NS"

# ZeRO-3 training code (mounted by job-qlora-zero3.yaml)
$K create configmap gh0stx-qlora-code \
  --from-file="$ROOT/train/entrypoint.sh" \
  --from-file="$ROOT/train/train_qlora.py" \
  --from-file="$ROOT/train/merge_adapter.py" \
  --from-file="$ROOT/train/repack_experts.py" \
  --from-file="$ROOT/train/ds_zero3.json" \
  -o yaml --dry-run=client | $K apply -f -

# graft-requant code (mounted by quant/job-quant.yaml)
$K create configmap gh0stx-quant-code \
  --from-file="$ROOT/quant/entrypoint_quant.sh" \
  --from-file="$ROOT/quant/graft_requant.py" \
  -o yaml --dry-run=client | $K apply -f -

echo "configmaps gh0stx-qlora-code, gh0stx-quant-code applied to $NS"
