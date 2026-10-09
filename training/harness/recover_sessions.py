#!/usr/bin/env python3
# Recover PASS trajectories from the opencode DB after the SQ/PRAGMA bug wrote degenerate
# rows. run_authored processes each task sequentially, so per-tid the i-th authored.log
# grade corresponds to the i-th DB session (both time-ordered). Re-export PASS sessions.
import sqlite3, json, subprocess, re, collections, os, sys
HOME=os.path.expanduser("~")
DB=f"{HOME}/.local/share/opencode/opencode.db"
LOG=f"{HOME}/octest/night/authored.log"
RULES=f"{HOME}/.config/opencode/gh0stx-rules.md"
EXPORT=f"{HOME}/octest/night/export_sft.py"
OUT=f"{HOME}/octest/night/dataset/sft.recovered.jsonl"

# 1. per-tid ordered grades from authored.log
log=collections.defaultdict(list)
for line in open(LOG, errors="replace"):
    m=re.match(r"^\d\d:\d\d:\d\d\s+(\S+)\s+t=\d+s\s+grade=(PASS|FAIL)", line)
    if m: log[m.group(1)].append(m.group(2))

# 2. per-tid ordered session ids from the DB
con=sqlite3.connect(DB)
sess=collections.defaultdict(list)
for sid,d,tc in con.execute(
    "SELECT id,directory,time_created FROM session WHERE directory LIKE '%/au\\_%' ESCAPE '\\' ORDER BY time_created"):
    mm=re.search(r"/au_(.+)_\d+_\d+$", d or "")
    if mm: sess[mm.group(1)].append(sid)

# 3. positional match; only where lengths align (guard against desync contamination)
open(OUT,"w").close()
recovered=collections.Counter(); skipped={}; logpass=collections.Counter()
for tid,grades in log.items():
    logpass[tid]=grades.count("PASS")
    sids=sess.get(tid,[])
    if len(sids)!=len(grades):
        skipped[tid]=(len(grades),len(sids));
        # fall back to min length only if close (<=2 off), else skip entirely
        if abs(len(sids)-len(grades))>2: continue
    n=min(len(grades),len(sids))
    for i in range(n):
        if grades[i]=="PASS":
            r=subprocess.run(["python3",EXPORT,DB,sids[i],RULES,OUT,
                              json.dumps({"authored":tid,"grade":"PASS"})],
                             capture_output=True,text=True,timeout=60)
            recovered[tid]+=1

# 4. validate: only keep real trajectories, report recovered vs log-PASS
good=0; degen=0
rows=[json.loads(l) for l in open(OUT) if l.strip()]
clean=[]
for r in rows:
    ms=r["messages"]; roles=[m["role"] for m in ms]
    if len(ms)>=3 and "user" in roles and roles.count("assistant")>=1 and (ms[-1].get("content") or "").strip():
        clean.append(r); good+=1
    else: degen+=1
open(OUT,"w").write("\n".join(json.dumps(r,ensure_ascii=False) for r in clean)+"\n")
print(f"recovered rows: {len(rows)} | real: {good} | degenerate: {degen}")
print("=== recovered PASS vs log PASS (sec_ vectors) ===")
for tid in sorted(logpass, key=lambda t:-logpass[t]):
    if tid.startswith("sec_"):
        print(f"  {recovered[tid]:3d}/{logpass[tid]:3d}  {tid}" + (f"  SKIP(len {skipped[tid]})" if tid in skipped else ""))
