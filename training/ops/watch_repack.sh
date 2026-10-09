#!/usr/bin/env bash
NS=distill; JOB=gh0stx-repack
for i in $(seq 1 130); do
  sleep 60
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.succeeded}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=SUCCEEDED at iter $i"; exit 0; fi
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.failed}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=FAILED at iter $i"
    P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[-1:].metadata.name}')
    kubectl -n $NS logs "$P" --tail=30 2>/dev/null | tr '\r' '\n' | grep -avE 'Completed [0-9]|remaining' | tail -20
    exit 1; fi
  P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  PH=$(kubectl -n $NS get pod "$P" -o jsonpath='{.status.phase}' 2>/dev/null)
  if kubectl -n $NS logs "$P" 2>/dev/null | grep -aq REPACK_DONE; then echo "RESULT=REPACK_DONE at iter $i"; exit 0; fi
  LASTL=$(kubectl -n $NS logs "$P" --tail=40 2>/dev/null | tr '\r' '\n' | grep -aE 'layer [0-9]+: E=|== |OK fused|Download|download:' | tail -1)
  echo "iter $i ph=${PH:-none} :: ${LASTL:-<no marker yet>}"
done
echo "RESULT=TIMEOUT"; exit 2
