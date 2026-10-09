#!/usr/bin/env python3
# Merge all tasks_*.json authored by the domain agents into one battery. Normalize the
# schema (some agents added behavior/notes), dedup ids, keep only self_test_passed, and
# re-check that every task carries the four fields the harvest runner needs.
import json, glob, os, collections

SCRATCH = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(os.path.dirname(SCRATCH), "tasks")
REQUIRED = ("id", "setup_sh", "prompt", "grader_sh")
files = sorted(glob.glob(os.path.join(TASKS_DIR, "tasks_*.json")))

merged, seen, per_domain, dropped = [], {}, collections.Counter(), []
for fp in files:
    dom = os.path.basename(fp)[len("tasks_"):-len(".json")]
    try:
        data = json.load(open(fp))
    except Exception as e:
        print(f"!! {dom}: unreadable ({e})"); continue
    tasks = data.get("tasks", data if isinstance(data, list) else [])
    for t in tasks:
        tid = t.get("id")
        miss = [k for k in REQUIRED if not t.get(k)]
        if miss:
            dropped.append((dom, tid, f"missing {miss}")); continue
        if not t.get("self_test_passed", False):
            dropped.append((dom, tid, "self_test not passed")); continue
        if tid in seen:
            dropped.append((dom, tid, f"dup of {seen[tid]}")); continue
        seen[tid] = dom
        t["domain"] = dom
        # reference field name varies; normalize to reference_solution_sh
        if "reference_solution_sh" not in t and "reference_sh" in t:
            t["reference_solution_sh"] = t.pop("reference_sh")
        merged.append(t)
        per_domain[dom] += 1

out = os.path.join(TASKS_DIR, "sec_tasks_all.json")
json.dump({"tasks": merged}, open(out, "w"), ensure_ascii=False, indent=1)

print(f"files merged: {len(files)}")
for d, n in sorted(per_domain.items()):
    print(f"  {d:12s} {n}")
print(f"TOTAL kept: {len(merged)}   dropped: {len(dropped)}")
for d, tid, why in dropped[:20]:
    print(f"  drop {d}/{tid}: {why}")
# sanity: how many carry a reference solution (needed for the fail->teach path)
has_ref = sum(1 for t in merged if t.get("reference_solution_sh"))
print(f"with reference_solution_sh: {has_ref}/{len(merged)}")
print(f"-> {out}")
