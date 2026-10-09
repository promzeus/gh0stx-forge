#!/usr/bin/env bash
# Quiet completion watcher: report only when the ZeRO-3 job succeeds (train+adapter+merge
# done, merged in S3) or fails. Horizon ~3.2h.
NS=distill; JOB=gh0stx-qlora-zero3
for i in $(seq 1 190); do
  sleep 60
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.succeeded}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=SUCCEEDED at iter $i (train+merge done)"
    aws s3 ls s3://ai-monitoring-test-123456789012/gh0stx-rft/merged/ 2>/dev/null | tail -4
    exit 0; fi
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.failed}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=FAILED at iter $i"
    P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[-1:].metadata.name}')
    kubectl -n $NS logs "$P" 2>/dev/null | tr '\r' '\n' | grep -avE 'Loading weights|Completed [0-9]|remaining|loss.:' \
      | grep -aiE 'error|traceback|out of memory|childfailed|assert|exitcode' | tail -20
    exit 1; fi
done
echo "RESULT=TIMEOUT"; exit 2
