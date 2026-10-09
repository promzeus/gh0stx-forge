#!/usr/bin/env python3
"""Author + self-validate BUG-FIX / EDIT-IN-PLACE gradeable tasks.

Each task: setup writes a buggy program + a failing test into $W; the model must
fix the bug IN PLACE so the test passes; grader re-checks independently and also
refuses any stray new source file; reference applies the correct fix.

Validation contract (HARD):
  setup -> grader            == FAIL
  setup -> reference -> grader == PASS
Only tasks satisfying both are kept with self_test_passed=true.
"""
import json, os, shutil, subprocess, tempfile, sys

SETUP_HDR = "#!/usr/bin/env bash\nset -e\n"
GRADER_HDR = "#!/usr/bin/env bash\ncd \"$W\" || exit 1\n"
REF_HDR = "#!/usr/bin/env bash\nset -e\ncd \"$W\" || exit 1\n"

def T(id, category, setup, prompt, grader, reference):
    return dict(id=id, category=category, behavior=category,
                setup_sh=SETUP_HDR + setup,
                prompt=prompt,
                grader_sh=GRADER_HDR + grader,
                reference_solution_sh=REF_HDR + reference,
                self_test_passed=False,
                notes="bugfix/edit-in-place harvest")

def stray_check(allowed, ext):
    # allowed: space-delimited basenames, with leading/trailing space for `case` match
    return (
        f"for f in $(find . -maxdepth 1 -name '*.{ext}'); do "
        f"b=$(basename \"$f\"); "
        f"case \" {allowed} \" in *\" $b \"*) ;; "
        f"*) echo \"FAIL: stray $b\"; exit 0;; esac; done\n"
    )

TASKS = []

# ---------------------------------------------------------------- 1 off-by-one
TASKS.append(T(
 "py_offbyone_chunk", "off-by-one",
 """cat > chunk.py <<'EOF'
def chunk(xs, size):
    out = []
    for i in range(0, len(xs) - size, size):
        out.append(xs[i:i + size])
    return out
EOF
cat > test_chunk.py <<'EOF'
from chunk import chunk
assert chunk([1, 2, 3, 4, 5, 6, 7], 3) == [[1, 2, 3], [4, 5, 6], [7]]
assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
assert chunk([], 3) == []
print("ok")
EOF
""",
 "chunk.py splits a list into fixed-size chunks but drops elements: the loop bound is off by one, so the final (and sometimes more) chunk is missing. Fix the bug in chunk.py in place so test_chunk.py passes. Do not rewrite from scratch or create new files.",
 stray_check("chunk.py test_chunk.py", "py") +
 """python3 - <<'PY'
import chunk
cases=[(([1,2,3,4,5,6,7],3),[[1,2,3],[4,5,6],[7]]),
       (([1,2,3,4],2),[[1,2],[3,4]]),
       (([],3),[]),
       (([1,2,3,4,5],2),[[1,2],[3,4],[5]]),
       (([1,2,3,4,5,6],3),[[1,2,3],[4,5,6]])]
ok=all(chunk.chunk(list(a),b)==e for (a,b),e in cases)
print('PASS' if ok else 'FAIL')
PY""",
 """cat > chunk.py <<'EOF'
def chunk(xs, size):
    out = []
    for i in range(0, len(xs), size):
        out.append(xs[i:i + size])
    return out
EOF"""))

# ---------------------------------------------------------- 2 wrong operator (go)
TASKS.append(T(
 "go_operator_clamp", "wrong-operator",
 """cat > go.mod <<'EOF'
module bug

go 1.21
EOF
cat > clamp.go <<'EOF'
package bug

func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return lo
	}
	return v
}
EOF
cat > clamp_test.go <<'EOF'
package bug

import "testing"

func TestClamp(t *testing.T) {
	cases := []struct{ v, lo, hi, want int }{
		{5, 0, 10, 5},
		{-3, 0, 10, 0},
		{20, 0, 10, 10},
		{10, 0, 10, 10},
		{0, 0, 10, 0},
	}
	for _, c := range cases {
		if got := Clamp(c.v, c.lo, c.hi); got != c.want {
			t.Fatalf("Clamp(%d,%d,%d)=%d want %d", c.v, c.lo, c.hi, got, c.want)
		}
	}
}
EOF
mkdir -p .pristine
cp clamp_test.go .pristine/clamp_test.go
""",
 "Clamp(v, lo, hi) in clamp.go should return hi when v exceeds hi, but the upper branch returns lo instead. Fix the wrong return in clamp.go in place so `go test` passes. Do not create new files or edit the test.",
 stray_check("clamp.go clamp_test.go", "go") +
 """# self-heal: drop a GOROOT that mismatches the go tool actually on PATH
if [ -n "$GOROOT" ] && [ "$(go env GOVERSION 2>/dev/null)" != "$(head -1 "$GOROOT/VERSION" 2>/dev/null)" ]; then unset GOROOT; fi
cp .pristine/clamp_test.go clamp_test.go
out=$(go test ./... 2>&1)
if printf '%s' "$out" | grep -q '^ok'; then echo PASS; else echo "FAIL: $out"; fi""",
 """cat > clamp.go <<'EOF'
package bug

func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}
EOF"""))

# ------------------------------------------------------- 3 mutable default arg
TASKS.append(T(
 "py_mutable_default", "mutable-default-arg",
 """cat > acc.py <<'EOF'
def collect(x, bucket=[]):
    bucket.append(x)
    return bucket
EOF
cat > test_acc.py <<'EOF'
from acc import collect
assert collect(1) == [1]
assert collect(2) == [2]
assert collect(3, [9]) == [9, 3]
print("ok")
EOF
""",
 "collect() in acc.py uses a mutable default argument, so results leak between calls with no bucket. Fix acc.py in place so each defaulted call starts fresh. Do not rewrite from scratch or create new files.",
 stray_check("acc.py test_acc.py", "py") +
 """python3 - <<'PY'
import acc
a=acc.collect(1)
b=acc.collect(2)
c=acc.collect(3,[9])
print('PASS' if a==[1] and b==[2] and c==[9,3] else 'FAIL: '+repr((a,b,c)))
PY""",
 """cat > acc.py <<'EOF'
def collect(x, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(x)
    return bucket
EOF"""))

# ---------------------------------------------- 4 integer division vs float (bash)
TASKS.append(T(
 "bash_intdiv_pct", "integer-division",
 """cat > pct.sh <<'EOF'
#!/usr/bin/env bash
# pct PART TOTAL -> integer percent (floor) of PART/TOTAL
pct() {
    local part=$1 total=$2
    echo $(( 100 * (part / total) ))
}
"$@"
EOF
cat > test_pct.sh <<'EOF'
#!/usr/bin/env bash
r=$(bash pct.sh pct 1 4); [ "$r" = 25 ] || { echo "fail 1/4=$r"; exit 1; }
r=$(bash pct.sh pct 3 8); [ "$r" = 37 ] || { echo "fail 3/8=$r"; exit 1; }
echo ok
EOF
""",
 "pct.sh computes a percentage but divides before multiplying, so integer division truncates PART/TOTAL to 0 whenever PART < TOTAL. Fix the arithmetic in pct.sh in place so the percent is correct. Do not create new files.",
 stray_check("pct.sh test_pct.sh", "sh") +
 """r1=$(bash pct.sh pct 1 4 2>/dev/null)
r2=$(bash pct.sh pct 3 8 2>/dev/null)
r3=$(bash pct.sh pct 5 5 2>/dev/null)
r4=$(bash pct.sh pct 7 10 2>/dev/null)
if [ "$r1" = 25 ] && [ "$r2" = 37 ] && [ "$r3" = 100 ] && [ "$r4" = 70 ]; then echo PASS; else echo "FAIL: $r1 $r2 $r3 $r4"; fi""",
 """cat > pct.sh <<'EOF'
#!/usr/bin/env bash
# pct PART TOTAL -> integer percent (floor) of PART/TOTAL
pct() {
    local part=$1 total=$2
    echo $(( (100 * part) / total ))
}
"$@"
EOF"""))

# ------------------------------------------------- 5 async/await misuse (node)
TASKS.append(T(
 "node_async_await", "async-await",
 """cat > sum.js <<'EOF'
function doubleAsync(n) {
  return new Promise((resolve) => resolve(n * 2));
}

async function sumDoubled(xs) {
  const doubled = xs.map(doubleAsync);
  let total = 0;
  for (const d of doubled) {
    total += d;
  }
  return total;
}

module.exports = { sumDoubled };
EOF
cat > test_sum.js <<'EOF'
const { sumDoubled } = require("./sum.js");
(async () => {
  const r = await sumDoubled([1, 2, 3]);
  if (r !== 12) { console.error("FAIL", r); process.exit(1); }
  console.log("ok");
})();
EOF
""",
 "sumDoubled() in sum.js maps an async function but never awaits the resulting promises, so it adds Promise objects instead of numbers. Fix sum.js in place so it awaits the results. Do not create new files.",
 stray_check("sum.js test_sum.js", "js") +
 """node -e '
const {sumDoubled}=require("./sum.js");
(async()=>{
  const r=await sumDoubled([1,2,3]);
  const r2=await sumDoubled([5,5]);
  const r3=await sumDoubled([]);
  console.log((r===12 && r2===20 && r3===0) ? "PASS" : "FAIL: "+r+" "+r2+" "+r3);
})();
'""",
 """cat > sum.js <<'EOF'
function doubleAsync(n) {
  return new Promise((resolve) => resolve(n * 2));
}

async function sumDoubled(xs) {
  const doubled = await Promise.all(xs.map(doubleAsync));
  let total = 0;
  for (const d of doubled) {
    total += d;
  }
  return total;
}

module.exports = { sumDoubled };
EOF"""))

# ---------------------------------------------------- 6 incorrect regex (python)
TASKS.append(T(
 "py_regex_greedy", "regex",
 """cat > extract.py <<'EOF'
import re


def first_tag(s):
    m = re.search(r'<(.+)>', s)
    return m.group(1) if m else None
EOF
cat > test_extract.py <<'EOF'
from extract import first_tag
assert first_tag("<a><b>") == "a"
assert first_tag("<div>x</div>") == "div"
assert first_tag("no tags") is None
print("ok")
EOF
""",
 "first_tag() in extract.py should return the name of the first <...> tag, but the greedy regex captures everything up to the last '>'. Fix the regex in extract.py in place so it stops at the first '>'. Do not create new files.",
 stray_check("extract.py test_extract.py", "py") +
 """python3 - <<'PY'
import extract
cases={'<a><b>':'a','<div>x</div>':'div','<x>':'x','no tags':None,'<p>hi</p>':'p'}
bad=[s for s,e in cases.items() if extract.first_tag(s)!=e]
print('PASS' if not bad else 'FAIL: '+str(bad))
PY""",
 """cat > extract.py <<'EOF'
import re


def first_tag(s):
    m = re.search(r'<([^>]+)>', s)
    return m.group(1) if m else None
EOF"""))

# ------------------------------------------ 7 wrong list mutation (python)
TASKS.append(T(
 "py_list_mutation", "list-mutation",
 """cat > filt.py <<'EOF'
def drop_negatives(xs):
    for x in xs:
        if x < 0:
            xs.remove(x)
    return xs
EOF
cat > test_filt.py <<'EOF'
from filt import drop_negatives
assert drop_negatives([-1, -2, 3]) == [3]
assert drop_negatives([-1, -1, -1]) == []
assert drop_negatives([1, 2, 3]) == [1, 2, 3]
print("ok")
EOF
""",
 "drop_negatives() in filt.py removes items from the list while iterating over it, so adjacent negatives get skipped and survive. Fix filt.py in place so all negatives are removed. Do not create new files.",
 stray_check("filt.py test_filt.py", "py") +
 """python3 - <<'PY'
import filt
cases=[([-1,-2,3],[3]),([-1,-1,-1],[]),([1,2,3],[1,2,3]),([-5,-6,-7,1],[1]),([2,-3,-4,-5,6],[2,6])]
bad=[inp for inp,exp in cases if filt.drop_negatives(list(inp))!=exp]
print('PASS' if not bad else 'FAIL: '+str(bad))
PY""",
 """cat > filt.py <<'EOF'
def drop_negatives(xs):
    for x in list(xs):
        if x < 0:
            xs.remove(x)
    return xs
EOF"""))

# ---------------------------------------------- 8 timezone/format bug (python)
TASKS.append(T(
 "py_format_strptime", "format",
 """cat > dt.py <<'EOF'
from datetime import datetime


def month_of(s):
    d = datetime.strptime(s, "%Y-%M-%d")
    return d.month
EOF
cat > test_dt.py <<'EOF'
from dt import month_of
assert month_of("2024-03-05") == 3
assert month_of("2020-12-31") == 12
print("ok")
EOF
""",
 "month_of() in dt.py parses a date but uses the wrong strptime directive for the month field, so it never reads the month. Fix the format string in dt.py in place. Do not create new files.",
 stray_check("dt.py test_dt.py", "py") +
 """python3 - <<'PY'
import dt
cases={'2024-03-05':3,'2020-12-31':12,'2024-07-15':7,'2019-11-02':11}
bad=[s for s,e in cases.items() if dt.month_of(s)!=e]
print('PASS' if not bad else 'FAIL: '+str(bad))
PY""",
 """cat > dt.py <<'EOF'
from datetime import datetime


def month_of(s):
    d = datetime.strptime(s, "%Y-%m-%d")
    return d.month
EOF"""))

# ------------------------------------------------ 9 recursion base case (python)
TASKS.append(T(
 "py_recursion_base", "recursion-base",
 """cat > rec.py <<'EOF'
def rsum(n):
    if n == 0:
        return 1
    return n + rsum(n - 1)
EOF
cat > test_rec.py <<'EOF'
from rec import rsum
assert rsum(0) == 0
assert rsum(3) == 6
print("ok")
EOF
""",
 "rsum(n) in rec.py sums 0..n but its base case returns the wrong value, so every result is off by one. Fix the base case in rec.py in place. Do not create new files.",
 stray_check("rec.py test_rec.py", "py") +
 """python3 - <<'PY'
import rec
cases={0:0,1:1,3:6,5:15,10:55}
bad=[n for n,e in cases.items() if rec.rsum(n)!=e]
print('PASS' if not bad else 'FAIL: '+str(bad))
PY""",
 """cat > rec.py <<'EOF'
def rsum(n):
    if n == 0:
        return 0
    return n + rsum(n - 1)
EOF"""))

# ---------------------------------------- 10 error handling swallows failure (go)
TASKS.append(T(
 "go_error_swallow", "error-handling",
 """cat > go.mod <<'EOF'
module bug

go 1.21
EOF
cat > parse.go <<'EOF'
package bug

import "strconv"

// SumInts parses each string as an int and returns the sum.
// It must return an error if any element is not a valid integer.
func SumInts(parts []string) (int, error) {
	total := 0
	for _, p := range parts {
		n, err := strconv.Atoi(p)
		if err == nil {
			return 0, err
		}
		total += n
	}
	return total, nil
}
EOF
cat > parse_test.go <<'EOF'
package bug

import "testing"

func TestSumInts(t *testing.T) {
	got, err := SumInts([]string{"1", "2", "3"})
	if err != nil || got != 6 {
		t.Fatalf("SumInts good = (%d,%v) want (6,nil)", got, err)
	}
	if _, err := SumInts([]string{"1", "x", "3"}); err == nil {
		t.Fatalf("SumInts with bad element should return an error")
	}
}
EOF
mkdir -p .pristine
cp parse_test.go .pristine/parse_test.go
""",
 "SumInts() in parse.go has its error check inverted: `if err == nil` returns early on valid input and silently swallows real parse errors. Fix the condition in parse.go in place so `go test` passes. Do not create new files or edit the test.",
 stray_check("parse.go parse_test.go", "go") +
 """# self-heal: drop a GOROOT that mismatches the go tool actually on PATH
if [ -n "$GOROOT" ] && [ "$(go env GOVERSION 2>/dev/null)" != "$(head -1 "$GOROOT/VERSION" 2>/dev/null)" ]; then unset GOROOT; fi
cp .pristine/parse_test.go parse_test.go
out=$(go test ./... 2>&1)
if printf '%s' "$out" | grep -q '^ok'; then echo PASS; else echo "FAIL: $out"; fi""",
 """cat > parse.go <<'EOF'
package bug

import "strconv"

// SumInts parses each string as an int and returns the sum.
// It must return an error if any element is not a valid integer.
func SumInts(parts []string) (int, error) {
	total := 0
	for _, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil {
			return 0, err
		}
		total += n
	}
	return total, nil
}
EOF"""))

# ------------------------------------------------ 11 wrong comparison (bash)
TASKS.append(T(
 "bash_numeric_compare", "wrong-comparison",
 """cat > cmp.sh <<'EOF'
#!/usr/bin/env bash
# maxof A B -> print the larger number
maxof() {
    if [[ $1 > $2 ]]; then
        echo "$1"
    else
        echo "$2"
    fi
}
"$@"
EOF
cat > test_cmp.sh <<'EOF'
#!/usr/bin/env bash
r=$(bash cmp.sh maxof 9 100); [ "$r" = 100 ] || { echo "fail 9,100=$r"; exit 1; }
r=$(bash cmp.sh maxof 100 9); [ "$r" = 100 ] || { echo "fail 100,9=$r"; exit 1; }
echo ok
EOF
""",
 "maxof() in cmp.sh compares numbers with `[[ $1 > $2 ]]`, which is a lexicographic string comparison ('9' > '100'). Fix cmp.sh in place to compare numerically. Do not create new files.",
 stray_check("cmp.sh test_cmp.sh", "sh") +
 """a=$(bash cmp.sh maxof 9 100 2>/dev/null)
b=$(bash cmp.sh maxof 100 9 2>/dev/null)
c=$(bash cmp.sh maxof 5 40 2>/dev/null)
d=$(bash cmp.sh maxof 40 5 2>/dev/null)
if [ "$a" = 100 ] && [ "$b" = 100 ] && [ "$c" = 40 ] && [ "$d" = 40 ]; then echo PASS; else echo "FAIL: $a $b $c $d"; fi""",
 """cat > cmp.sh <<'EOF'
#!/usr/bin/env bash
# maxof A B -> print the larger number
maxof() {
    if (( $1 > $2 )); then
        echo "$1"
    else
        echo "$2"
    fi
}
"$@"
EOF"""))

# ------------------------------------- 12 retry loop reuses state (node)
TASKS.append(T(
 "node_retry_state", "retry-loop",
 """cat > retry.js <<'EOF'
// makeSource returns loadPage(i); the first call for i === failOnce throws,
// a retry of the same index then succeeds.
function makeSource(failOnce) {
  const failed = {};
  return function loadPage(i) {
    if (i === failOnce && !failed[i]) {
      failed[i] = true;
      throw new Error("transient");
    }
    return i;
  };
}

// fetchAll loads pages 0..pages-1, retrying the whole fetch up to 3 times.
function fetchAll(pages, loadPage) {
  const out = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      for (let i = 0; i < pages; i++) {
        out.push(loadPage(i));
      }
      return out;
    } catch (e) {
      // retry the whole fetch
    }
  }
  return out;
}

module.exports = { makeSource, fetchAll };
EOF
cat > test_retry.js <<'EOF'
const { makeSource, fetchAll } = require("./retry.js");
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const r = fetchAll(4, makeSource(2));
if (!eq(r, [0, 1, 2, 3])) { console.error("FAIL", r); process.exit(1); }
console.log("ok");
EOF
""",
 "fetchAll() in retry.js accumulates results in a list declared outside the retry loop, so pages loaded before a mid-fetch failure are kept and then loaded again on retry, producing duplicates. Fix retry.js in place so a retry starts from a clean result. Do not create new files.",
 stray_check("retry.js test_retry.js", "js") +
 """node -e '
const {makeSource, fetchAll}=require("./retry.js");
const eq=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const r1=fetchAll(4, makeSource(2));
const r2=fetchAll(3, makeSource(0));
const r3=fetchAll(3, makeSource(-1));
const r4=fetchAll(5, makeSource(3));
console.log((eq(r1,[0,1,2,3])&&eq(r2,[0,1,2])&&eq(r3,[0,1,2])&&eq(r4,[0,1,2,3,4]))?"PASS":"FAIL: "+JSON.stringify([r1,r2,r3,r4]));
'""",
 """cat > retry.js <<'EOF'
// makeSource returns loadPage(i); the first call for i === failOnce throws,
// a retry of the same index then succeeds.
function makeSource(failOnce) {
  const failed = {};
  return function loadPage(i) {
    if (i === failOnce && !failed[i]) {
      failed[i] = true;
      throw new Error("transient");
    }
    return i;
  };
}

// fetchAll loads pages 0..pages-1, retrying the whole fetch up to 3 times.
function fetchAll(pages, loadPage) {
  const out = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      out.length = 0;
      for (let i = 0; i < pages; i++) {
        out.push(loadPage(i));
      }
      return out;
    } catch (e) {
      // retry the whole fetch
    }
  }
  return out;
}

module.exports = { makeSource, fetchAll };
EOF"""))


# ------------------------------------------------------------ validation harness
def run(script, W, timeout=90):
    p = subprocess.run(["bash", "-c", script], cwd=W,
                       env={**os.environ, "W": W},
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def validate(task):
    # phase 1: setup -> grader  == FAIL
    d1 = tempfile.mkdtemp(prefix="bf1_")
    try:
        rc, o, e = run(task["setup_sh"], d1)
        if rc != 0:
            return False, f"setup failed: {e.strip() or o.strip()}"
        rc, o, e = run(task["grader_sh"], d1)
        buggy_out = o.strip()
        if not buggy_out.startswith("FAIL") and buggy_out != "FAIL":
            return False, f"grader on buggy did not FAIL: out={buggy_out!r} err={e.strip()!r}"
        if "PASS" in buggy_out:
            return False, f"grader on buggy contained PASS: {buggy_out!r}"
    finally:
        shutil.rmtree(d1, ignore_errors=True)
    # phase 2: setup -> reference -> grader == PASS
    d2 = tempfile.mkdtemp(prefix="bf2_")
    try:
        rc, o, e = run(task["setup_sh"], d2)
        if rc != 0:
            return False, f"setup(2) failed: {e.strip() or o.strip()}"
        rc, o, e = run(task["reference_solution_sh"], d2)
        if rc != 0:
            return False, f"reference failed: {e.strip() or o.strip()}"
        rc, o, e = run(task["grader_sh"], d2)
        fixed_out = o.strip()
        if fixed_out != "PASS":
            return False, f"grader after reference != PASS: out={fixed_out!r} err={e.strip()!r}"
    finally:
        shutil.rmtree(d2, ignore_errors=True)
    return True, "ok"

def main():
    kept = []
    report = []
    for t in TASKS:
        ok, msg = validate(t)
        report.append((t["id"], t["category"], ok, msg))
        if ok:
            t["self_test_passed"] = True
            kept.append(t)
    out_path = "/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_bugfix.json"
    with open(out_path, "w") as f:
        json.dump({"tasks": kept}, f, indent=1)
    print("=== VALIDATION REPORT ===")
    for id, cat, ok, msg in report:
        print(f"[{'PASS' if ok else 'FAIL'}] {id:24s} ({cat}) {'' if ok else '-> '+msg}")
    print(f"\nkept {len(kept)}/{len(TASKS)} -> {out_path}")

if __name__ == "__main__":
    main()
