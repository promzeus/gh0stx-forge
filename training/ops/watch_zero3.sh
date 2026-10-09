#!/usr/bin/env bash
# Watch ZeRO-3 until the first optimizer step logs (proves it trains on this model),
# the job fails, or ~2.3h elapse. Downloading the 282GB fused base takes ~40min first.
NS=distill; JOB=gh0stx-qlora-zero3
for i in $(seq 1 140); do
  sleep 60
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.succeeded}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=SUCCEEDED (full pipeline incl merge) at iter $i"; exit 0; fi
  if [ "$(kubectl -n $NS get job $JOB -o jsonpath='{.status.failed}' 2>/dev/null)" = "1" ]; then
    echo "RESULT=FAILED at iter $i"
    P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[-1:].metadata.name}')
    kubectl -n $NS logs "$P" 2>/dev/null | tr '\r' '\n' | grep -avE 'Loading weights|Completed [0-9]|remaining' \
      | grep -aiE 'error|traceback|out of memory|childfailed|assert|deepspeed|exitcode|runtime' | tail -20
    exit 1; fi
  P=$(kubectl -n $NS get pod -l app=$JOB -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  PH=$(kubectl -n $NS get pod "$P" -o jsonpath='{.status.phase}' 2>/dev/null)
  LOSS=$(kubectl -n $NS logs "$P" 2>/dev/null | grep -aoE "\{'loss': [0-9.]+[^}]*\}" | tail -1)
  if [ -n "$LOSS" ]; then
    STEP=$(kubectl -n $NS logs "$P" 2>/dev/null | grep -aoE '[0-9]+/540' | tail -1)
    echo "RESULT=TRAINING_STEPPING at iter $i step=$STEP loss=$LOSS"; exit 0; fi
  MARK=$(kubectl -n $NS logs "$P" --tail=60 2>/dev/null | tr '\r' '\n' | grep -aE 'ZERO3 rank=0|zero.Init|training \(zero3\)|adapted matrices|download|== ' | tail -1)
  echo "iter $i ph=${PH:-none} :: ${MARK:-<provisioning/pending>}"
done
echo "RESULT=TIMEOUT"; exit 2
