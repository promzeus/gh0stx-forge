#!/usr/bin/env python3
"""Contract validator (spec section 2). A task is valid iff:
  setup -> grader                 == FAIL   (task is not already solved)
  setup -> reference -> grader    == PASS   (reference solves it, grader catches it)
usage: validate_task.py <tasks.json> <task_id>
exit 0 = VALID, 1 = INVALID, 2 = not found.
"""
import json, sys, os, subprocess, tempfile, shutil


def run(script, W, timeout=60):
    env = dict(os.environ); env["W"] = W
    p = subprocess.run(["bash", "-c", script], cwd=W, env=env,
                       capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr).strip()


def main():
    tasks = json.load(open(sys.argv[1]))
    tasks = tasks.get("tasks", tasks) if isinstance(tasks, dict) else tasks
    t = next((x for x in tasks if x.get("id") == sys.argv[2]), None)
    if not t:
        print(f"{sys.argv[2]}: not found"); sys.exit(2)
    W = tempfile.mkdtemp(prefix="validate_")
    try:
        run(t["setup_sh"], W); bare = run(t["grader_sh"], W)
        run(t["reference_solution_sh"], W); ref = run(t["grader_sh"], W)
    finally:
        shutil.rmtree(W, ignore_errors=True)
    unsolved = bare.split()[:1] != ["PASS"]
    solved = ref.split()[:1] == ["PASS"]
    ok = unsolved and solved
    print(f"{sys.argv[2]}: unsolved={'ok' if unsolved else 'BAD'} "
          f"reference={'ok' if solved else 'BAD'} -> {'VALID' if ok else 'INVALID'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
