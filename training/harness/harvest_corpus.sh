#!/usr/bin/env bash
# Harvest a multi-example verified corpus (spec section 35). repair_cycle writes a line only
# on Student FAIL -> Teacher PASS, so it self-targets weak spots. Held-out ids are excluded.
# usage: harvest_corpus.sh   (needs DEEPSEEK_API_KEY; sourced from ~/.zshrc)
set -u
source ~/.zshrc 2>/dev/null || true
cd "$(dirname "$0")"
OUT=/tmp/corpus_sft.jsonl
: > "$OUT"
IDS="node_async_await py_format_strptime node_retry_state alg_edit_distance alg_max_subarray alg_interval_union alg_coin_change_ways alg_knapsack01"
for id in $IDS; do
  echo "=== $id $(date +%H:%M:%S) ==="
  STUDENT_TURNS=12 python3 repair_cycle.py tasks/safe_subset.json "$id" "$OUT" 2>&1 \
    | grep -E "\[student\]|\[teacher|\[verified\]|\[discarded\]|not found|not set"
done
echo "=== corpus lines: $(wc -l < "$OUT" 2>/dev/null) ==="
