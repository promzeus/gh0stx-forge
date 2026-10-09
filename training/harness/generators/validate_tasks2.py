#!/usr/bin/env python3
"""Validate cloud/container tasks: setup->grader must NOT PASS;
setup->reference->grader must PASS. Prints a per-task report and rewrites JSON
keeping only self-tests that pass (self_test_passed=true)."""
import json, os, subprocess, tempfile, shutil, sys, signal

JSON_PATH = "/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_cloud.json"

def run(script, W, timeout=60):
    env = dict(os.environ)
    env["W"] = W
    p = subprocess.run(["bash", "-c", script], cwd=W, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def is_pass(out):
    return any(line.strip() == "PASS" for line in out.splitlines())

def kill_server(W):
    pid_file = os.path.join(W, "server.pid")
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

def validate(task):
    rid = task["id"]
    # ---- negative: setup -> grader must NOT pass ----
    Wn = tempfile.mkdtemp(prefix="neg_")
    try:
        rc, so, se = run(task["setup_sh"], Wn)
        if rc != 0:
            return False, f"setup(neg) rc={rc} stderr={se[:300]}"
        rc, so, se = run(task["grader_sh"], Wn)
        neg_out = so.strip()
        if is_pass(so):
            return False, f"NEGATIVE grader PASSed unexpectedly: {neg_out[:200]}"
    finally:
        kill_server(Wn)
        shutil.rmtree(Wn, ignore_errors=True)
    # ---- positive: setup -> reference -> grader must pass ----
    Wp = tempfile.mkdtemp(prefix="pos_")
    try:
        rc, so, se = run(task["setup_sh"], Wp)
        if rc != 0:
            return False, f"setup(pos) rc={rc} stderr={se[:300]}"
        rc, so, se = run(task["reference_solution_sh"], Wp)
        if rc != 0:
            return False, f"reference rc={rc} stderr={se[:300]} stdout={so[:200]}"
        rc, so, se = run(task["grader_sh"], Wp)
        pos_out = so.strip()
        if not is_pass(so):
            return False, f"POSITIVE grader did not PASS: {pos_out[:200]} stderr={se[:200]}"
    finally:
        kill_server(Wp)
        shutil.rmtree(Wp, ignore_errors=True)
    return True, f"neg='{neg_out[:60]}' pos='{pos_out[:60]}'"

def main():
    data = json.loads(subprocess.run(
        ["python3", "/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_cloud_gen.py"],
        capture_output=True, text=True, check=True).stdout)
    tasks = data["tasks"]
    kept = []
    for t in tasks:
        ok, msg = validate(t)
        t["self_test_passed"] = bool(ok)
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {t['id']}: {msg}")
        if ok:
            kept.append(t)
    with open(JSON_PATH, "w") as f:
        json.dump({"tasks": kept}, f, indent=1)
    print(f"\nValidated {len(kept)}/{len(tasks)} tasks -> {JSON_PATH}")

if __name__ == "__main__":
    main()
