#!/usr/bin/env bash
# Wait for the ZeRO-3 v4 round to finish, then wake me to run requant->assemble->deploy->AT.
NS=distill; JOB=gh0stx-qlora-zero3
MERGED=s3://ai-monitoring-test-123456789012/gh0stx-rft/merged/model.safetensors.index.json
kq() { for i in 1 2 3 4; do R=$(kubectl -n $NS get job $JOB -o jsonpath="$1" 2>/dev/null) && [ -n "$R" ] && { echo "$R"; return; }; sleep 8; done; echo ""; }
for i in $(seq 1 600); do
  sleep 300
  S=$(kq '{.status.succeeded}'); F=$(kq '{.status.failed}')
  if [ "$S" = "1" ]; then
    echo "RESULT=SUCCEEDED at iter $i"
    aws s3 ls "$MERGED" 2>/dev/null
    aws s3 ls s3://ai-monitoring-test-123456789012/gh0stx-rft/adapter/DONE 2>/dev/null
    exit 0
  fi
  if [ "$F" = "1" ]; then echo "RESULT=FAILED at iter $i (spot reclaim? relaunch needed)"; exit 1; fi
  # progress line
  P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  L=$(kubectl -n $NS logs "$P" --tail=60 2>/dev/null | tr '\r' '\n' | grep -aoE "'epoch': '[0-9.]+'" | tail -1)
  echo "iter $i ($((i*5))min): ${L:-<no epoch>}"
done
echo "RESULT=TIMEOUT after ~10.8h"; exit 2
