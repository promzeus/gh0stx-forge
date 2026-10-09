#!/usr/bin/env python3
import json, os, subprocess, tempfile, shutil

d = json.load(open("tasks2_protocol.json"))
tasks = d["tasks"]

def run(script, W):
    env = dict(os.environ); env["W"] = W
    p = subprocess.run(["bash", "-c", script], cwd=W, env=env,
                       capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout, p.stderr

def first_tok(s):
    s = s.strip()
    return s.split()[0] if s else ""

allpass = True
results = []
for t in tasks:
    W = tempfile.mkdtemp(prefix="ptask_")
    try:
        rc, so, se = run(t["setup_sh"], W)
        setup_ok = rc == 0
        # grader BEFORE solving -> must NOT be PASS
        rc2, so2, se2 = run(t["grader_sh"], W)
        pre = first_tok(so2)
        pre_not_pass = (pre != "PASS")
        # reference
        rc3, so3, se3 = run(t["reference_solution_sh"], W)
        ref_ok = rc3 == 0
        # grader AFTER
        rc4, so4, se4 = run(t["grader_sh"], W)
        post = first_tok(so4)
        post_pass = (post == "PASS")
        ok = setup_ok and pre_not_pass and ref_ok and post_pass
        allpass = allpass and ok
        results.append((t["id"], ok, dict(setup_ok=setup_ok, pre=pre, ref_ok=ref_ok,
                                          post=post,
                                          setup_err=se.strip()[-300:] if not setup_ok else "",
                                          ref_err=se3.strip()[-300:] if not ref_ok else "",
                                          grader_err=se4.strip()[-300:] if not post_pass else "",
                                          pre_out=so2.strip()[-200:],
                                          post_out=so4.strip()[-200:])))
    finally:
        shutil.rmtree(W, ignore_errors=True)

for tid, ok, info in results:
    print(("PASS " if ok else "FAIL ") + tid)
    if not ok:
        for k, v in info.items():
            if v:
                print("    %s = %r" % (k, v))
print("\n%d/%d self_test_passed" % (sum(1 for _, ok, _ in results if ok), len(results)))
