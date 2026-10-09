#!/usr/bin/env bash
# Full-corpus harvest (spec section 35). repair_cycle over all safe_subset tasks except the
# 7 held-out eval tasks. Writes a verified line only on Student FAIL -> Teacher PASS. Resumable:
# a task id in the .done file is skipped, OUT is never truncated, so a re-run continues.
# No serve outage (Student on live serve, Teacher DeepSeek). usage: harvest_full.sh
set -u
source ~/.zshrc 2>/dev/null || true
cd "$(dirname "$0")"
OUT=/tmp/full_corpus_sft.jsonl
DONE=/tmp/full_corpus.done
touch "$OUT" "$DONE"
HELD="go_operator_clamp py_mutable_default bash_intdiv_pct py_list_mutation py_recursion_base go_error_swallow bash_numeric_compare"
IDS=$(HELD="$HELD" python3 - <<'PY'
import json, os
t=json.load(open("tasks/safe_subset.json")); t=t.get("tasks",t) if isinstance(t,dict) else t
held=set(os.environ["HELD"].split()); seen=set()
for x in t:
    i=x.get("id")
    if i and i not in held and i not in seen:
        seen.add(i); print(i)
PY
)
total=$(echo "$IDS" | wc -w); n=0
echo "=== full harvest start $(date +%H:%M:%S): $total tasks (held-out excluded) ==="
for id in $IDS; do
  n=$((n+1))
  grep -qxF "$id" "$DONE" && { echo "[$n/$total] skip done $id"; continue; }
  echo "=== [$n/$total] $id $(date +%H:%M:%S) ==="
  STUDENT_TURNS=12 python3 repair_cycle.py tasks/safe_subset.json "$id" "$OUT" 2>&1 \
    | grep -E "\[student\]|\[teacher|\[verified\]|\[discarded\]|not found|not set"
  echo "$id" >> "$DONE"
done
echo "=== full harvest done $(date +%H:%M:%S): $(wc -l < "$OUT") verified from $total tasks ==="
