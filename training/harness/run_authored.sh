#!/usr/bin/env bash
# Hardened harvester for workflow-authored tasks. A single hung opencode run or a
# locked DB cannot wedge the loop: every sqlite call is timeout+busy_timeout bounded,
# every task has a hard wall-clock cap, and cleanup kills by unique dir marker.
# usage: run_authored.sh <deadline_epoch>
set +e
OC="$HOME/.opencode/bin/opencode"; DB="$HOME/.local/share/opencode/opencode.db"
BASE="$HOME/octest/night"; RULES="$HOME/.config/opencode/gh0stx-rules.md"
OUT="$BASE/dataset/sft.jsonl"; mkdir -p "$BASE/dataset"
DEADLINE="${1:?need deadline}"; AD="$BASE/authored"
TASK_HARD=280               # hard seconds per task

# bounded sqlite: never blocks the loop (8s wall cap, 3s busy wait for locks)
SQ() { timeout 8 sqlite3 -cmd ".timeout 3000" "$DB" "$1" 2>/dev/null; }

python3 - "$BASE/authored_tasks.json" "$AD" <<'PY'
import json, sys, os
tasks=json.load(open(sys.argv[1]))["tasks"]; d=sys.argv[2]; os.makedirs(d,exist_ok=True)
ids=[]
for t in tasks:
    if not t.get("self_test_passed"): continue
    tid=t["id"]; ids.append(tid)
    open(f"{d}/{tid}.setup","w").write(t["setup_sh"])
    open(f"{d}/{tid}.grader","w").write(t["grader_sh"])
    open(f"{d}/{tid}.prompt","w").write(t["prompt"])
    open(f"{d}/{tid}.cat","w").write(t.get("category","authored"))
open(f"{d}/ids.txt","w").write("\n".join(ids))
print(len(ids),"authored tasks materialized")
PY
mapfile -t IDS < "$AD/ids.txt"

run_one() {
    local tid="$1"
    local mark="au_${tid}_$$_$RANDOM"
    local W="$BASE/$mark"
    rm -rf "$W"; mkdir -p "$W"; cd "$W" || return
    W="$W" timeout 30 bash "$AD/$tid.setup" >/dev/null 2>&1
    local PROMPT; PROMPT=$(cat "$AD/$tid.prompt")
    local CAT; CAT=$(cat "$AD/$tid.cat")
    local LAUNCH; LAUNCH=$(( $(date +%s%3N) - 1000 ))
    nohup "$OC" run -m gx10/gh0stx --dir "$W" --format json "$PROMPT" >"$W/out.json" 2>"$W/err.txt" &
    local PID=$!; local SID="" last=-1 stable=0 t=0 n lt ntool ntext started
    while [ $t -lt $TASK_HARD ]; do
        sleep 12; t=$((t+12))
        [ -z "$SID" ] && SID=$(SQ "SELECT id FROM session WHERE directory LIKE '%/$mark%' AND time_created>=$LAUNCH ORDER BY time_created DESC LIMIT 1;")
        [ -z "$SID" ] && { kill -0 $PID 2>/dev/null || break; continue; }
        n=$(SQ "SELECT count(*) FROM part WHERE session_id='$SID';")
        lt=$(SQ "SELECT json_extract(data,'\$.type') FROM part WHERE session_id='$SID' ORDER BY time_created DESC LIMIT 1;")
        ntool=$(SQ "SELECT count(*) FROM part WHERE session_id='$SID' AND json_extract(data,'\$.type')='tool';")
        ntext=$(SQ "SELECT count(*) FROM part WHERE session_id='$SID' AND json_extract(data,'\$.type')='text';")
        started=0; { [ "${ntool:-0}" -gt 0 ] || [ "${ntext:-0}" -ge 2 ]; } && started=1
        if [ "$n" = "$last" ]; then stable=$((stable+1)); else stable=0; fi
        last=$n
        if [ $started -eq 1 ] && [ $stable -ge 2 ] && { [ "$lt" = "text" ] || [ "$lt" = "step-finish" ]; }; then break; fi
        kill -0 $PID 2>/dev/null || break
    done
    # thorough cleanup: PID, its children, and anything still bound to this work dir
    kill $PID 2>/dev/null; pkill -P $PID 2>/dev/null; pkill -f "$mark" 2>/dev/null; sleep 1
    kill -9 $PID 2>/dev/null; pkill -9 -f "$mark" 2>/dev/null
    local G; G=$(W="$W" timeout 30 bash "$AD/$tid.grader" 2>/dev/null)
    if [ -n "$SID" ] && { case "$G" in PASS*) true;; *) false;; esac; }; then
        timeout 30 python3 "$BASE/export_sft.py" "$DB" "$SID" "$RULES" "$OUT" "{\"task\":\"$CAT\",\"authored\":\"$tid\",\"grade\":\"PASS\"}" >>"$BASE/export.log" 2>&1
    fi
    echo "$(date +%T) $tid t=${t}s grade=${G%%(*} collected=$(wc -l < "$OUT" 2>/dev/null)" >> "$BASE/authored.log"
    rm -rf "$W"
}

echo "authored harvest start $(date) hard=${TASK_HARD}s" >> "$BASE/authored.log"
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    [ -f "$BASE/STOP_AUTH" ] && break
    for tid in "${IDS[@]}"; do
        [ "$(date +%s)" -ge "$DEADLINE" ] && break
        [ -f "$BASE/STOP_AUTH" ] && break
        run_one "$tid" 2>>"$BASE/authored.err"
    done
done
echo "authored harvest end $(date) total=$(wc -l < "$OUT" 2>/dev/null)" >> "$BASE/authored.log"
