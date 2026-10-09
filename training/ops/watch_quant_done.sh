#!/usr/bin/env bash
NS=distill; JOB=gh0stx-quant-graft
kq() { for i in 1 2 3 4; do R=$(kubectl -n $NS get job $JOB -o jsonpath="$1" 2>/dev/null) && [ -n "$R" ] && { echo "$R"; return; }; sleep 8; done; echo ""; }
for i in $(seq 1 60); do
  sleep 120
  S=$(kq '{.status.succeeded}'); F=$(kq '{.status.failed}')
  if [ "$S" = "1" ]; then
    echo "RESULT=SUCCEEDED at iter $i"
    aws s3 ls s3://ai-monitoring-test-123456789012/gh0stx-rft/graft/ 2>/dev/null
    exit 0
  fi
  if [ "$F" = "1" ]; then echo "RESULT=FAILED at iter $i"
    P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[-1:].metadata.name}')
    kubectl -n $NS logs "$P" 2>/dev/null | tr '\r' '\n' | grep -aiE 'error|traceback|assert|missing scales|no candidate' | tail -8
    exit 1; fi
  P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  M=$(kubectl -n $NS logs "$P" --tail=30 2>/dev/null | tr '\r' '\n' | grep -aE 'preflight|graft imports OK|sync merged|quant [0-9]+/|wrote model-graft|== ' | tail -1)
  echo "iter $i ($((i*2))min) ph=$(kubectl -n $NS get pod "$P" -o jsonpath='{.status.phase}' 2>/dev/null) :: ${M:-<provisioning>}"
done
echo "RESULT=TIMEOUT"; exit 2
