#!/usr/bin/env bash
# Supervise run_authored.sh in a loop until TARGET raw sft rows or a STOP_HARVEST file.
# Survives ssh drops (run detached via nohup setsid). Stop: touch STOP_HARVEST (ends after
# the current cycle); add STOP_AUTH to also cut the current cycle immediately.
cd "$HOME/octest/night" || exit 1
TARGET=${1:-2000}      # raw dataset/sft.jsonl rows to reach
CYCLE=${2:-3600}       # per-cycle harvest window, seconds
LOG="$HOME/octest/night/supervisor.log"
echo "supervisor start $(date) target=$TARGET cycle=${CYCLE}s" >> "$LOG"
while true; do
  [ -f STOP_HARVEST ] && { echo "STOP_HARVEST seen $(date)" >> "$LOG"; break; }
  rows=$(wc -l < dataset/sft.jsonl 2>/dev/null || echo 0)
  if [ "${rows:-0}" -ge "$TARGET" ]; then echo "target reached $rows >= $TARGET $(date)" >> "$LOG"; break; fi
  rm -f STOP_AUTH
  DL=$(( $(date +%s) + CYCLE ))
  echo "cycle start $(date) rows=$rows deadline=$(date -d @"$DL" +%H:%M)" >> "$LOG"
  bash run_authored.sh "$DL" >> /tmp/authored.out 2>&1
  echo "cycle end $(date) rows=$(wc -l < dataset/sft.jsonl)" >> "$LOG"
done
echo "supervisor end $(date) rows=$(wc -l < dataset/sft.jsonl)" >> "$LOG"
