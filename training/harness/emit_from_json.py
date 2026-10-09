import json,os,sys,subprocess,tempfile,shutil
src, dst = sys.argv[1], sys.argv[2]
tasks=json.load(open(src))
if isinstance(tasks, dict): tasks = tasks.get("tasks", [])
def run(s,W):
    e=dict(os.environ);e["W"]=W
    return subprocess.run(["bash","-c",s],env=e,cwd=W,capture_output=True,text=True,timeout=120)
passed=[]
for t in tasks:
    W=tempfile.mkdtemp()
    try:
        run(t["setup_sh"],W); run(t["reference_solution_sh"],W); g=run(t["grader_sh"],W)
        v=(g.stdout.strip().splitlines() or ["x"])[-1]
        ok=v.startswith("PASS"); print(("PASS" if ok else "FAIL"), t["id"], "" if ok else v)
        if ok: passed.append(t)
    finally: shutil.rmtree(W,ignore_errors=True)
d=json.load(open(dst)); have={x["id"] for x in d["tasks"]}; added=[]
for t in passed:
    if t["id"] in have: continue
    t["self_test_passed"]=True
    for k in ["id","category","behavior","setup_sh","prompt","grader_sh","reference_solution_sh","self_test_passed"]:
        t.setdefault(k, t.get(k,""))
    d["tasks"].append(t); added.append(t["id"])
json.dump(d, open(dst,"w"), ensure_ascii=False, indent=1)
print("appended:", added, "| total:", len(d["tasks"]))
