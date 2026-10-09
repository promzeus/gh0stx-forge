#!/usr/bin/env bash
# Held-out baseline: run bugfix-class tasks (not in harvest) against the served MODEL,
# record grade per task. No SFT is trained here; this is the before-number.
# usage: MODEL=gh0stx-r2 heldout_eval.sh
set -u
cd "$(dirname "$0")"
MODEL="${MODEL:-gh0stx-r2}"
TASKS=tasks/safe_subset.json
IDS="go_operator_clamp py_mutable_default bash_intdiv_pct py_list_mutation py_recursion_base go_error_swallow bash_numeric_compare"
pass=0; total=0
for id in $IDS; do
  total=$((total+1))
  line=$(MODEL="$MODEL" MAX_TURNS=20 python3 run_task.py "$TASKS" "$id" "/tmp/heldout_${id}.jsonl" 2>&1 | grep -E "turns=.*grade=" | tail -1)
  echo "$id | $line"
  echo "$line" | grep -q "grade=PASS" && pass=$((pass+1))
done
echo "SUMMARY MODEL=$MODEL pass=$pass/$total"
