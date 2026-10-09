#!/usr/bin/env python3
"""Author + self-test CTF-style reverse-engineering puzzles for the harness.
Each task: setup plants an artifact, model recovers something, grader re-checks
deterministically, reference recovers it. Validation: setup->grader != PASS,
setup->reference->grader == PASS. Keeps only passing tasks."""
import json, os, shutil, subprocess, tempfile, textwrap

SCRATCH = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SCRATCH, "tasks2_re.json")

SETUP_HDR = "#!/usr/bin/env bash\nset -e\n"
GRADER_HDR = "#!/usr/bin/env bash\ncd \"$W\" || exit 1\n"
REF_HDR = "#!/usr/bin/env bash\nset -e\ncd \"$W\" || exit 1\n"

def T(id, category, setup, prompt, grader, reference):
    return dict(id=id, category=category,
                setup_sh=SETUP_HDR + setup,
                prompt=prompt,
                grader_sh=GRADER_HDR + grader,
                reference_solution_sh=REF_HDR + reference,
                self_test_passed=False,
                notes="ctf/reverse-engineering harvest")

# ---- authoring-time helpers to compute embedded constants ----------------
def build_stack_prog(flag, k):
    prog = []
    for ch in flag:
        prog += [0, ord(ch) ^ k, 0, k, 2, 3]
    prog += [6]
    return prog

def reg_run(x, PROG):
    r = [x, 0, 0, 0]; out = None
    for ins in PROG:
        op = ins[0]
        if op == 0: r[ins[1]] = ins[2]
        elif op == 1: r[ins[1]] = (r[ins[1]] + r[ins[2]]) & 0xffffffff
        elif op == 2: r[ins[1]] = r[ins[1]] ^ ins[2]
        elif op == 3: r[ins[1]] = (r[ins[1]] * ins[2]) & 0xffffffff
        elif op == 4: r[ins[1]] = (r[ins[1]] << ins[2]) & 0xffffffff
        elif op == 5: r[ins[1]] = r[ins[1]] & ins[2]
        elif op == 6: r[ins[1]] = r[ins[2]]
        elif op == 7: out = r[ins[1]]
        elif op == 8: break
    return out

def sm_f(n):
    state = 0; acc = 0; x = n
    while True:
        if state == 0: acc = x + 7; state = 3
        elif state == 3: acc = acc ^ 0x2a; state = 1
        elif state == 1: state = 2 if acc % 2 == 0 else 4
        elif state == 2: acc = (acc * 3) & 0xffff; state = 5
        elif state == 4: acc = (acc + 100) & 0xffff; state = 5
        elif state == 5: return acc

def transform_encode(s):
    out = []
    for i, c in enumerate(s):
        v = (ord(c) + (i * 7) % 26) & 0xff
        v ^= 0x3c
        out.append("%02x" % v)
    return "".join(out)

def pre_g(x):
    y = x
    y = (y * 1103515245 + 12345) & 0x7fffffff
    y ^= (y >> 7)
    y = (y * 2654435761) & 0xffffffff
    y ^= (y >> 11)
    return y & 0xffffff

def auth_H(pw):
    h = 0x1505
    for c in pw:
        h = ((h << 5) + h + ord(c)) & 0xffffffff
    h ^= (h >> 13)
    return h & 0xffffff

# ---- computed constants --------------------------------------------------
STACK_FLAG = "FLAG{st4ck_trace}"
STACK_PROG = build_stack_prog(STACK_FLAG, 0x5a)

REG_PROG = [(6,1,0),(3,1,3),(2,1,0xABCD),(6,2,0),(1,2,1),(4,2,1),(5,2,0xffff),(6,0,2),(7,0),(8,)]
REG_INPUT = 1337
REG_EXPECT = reg_run(REG_INPUT, REG_PROG)

SM_INPUT = 12345
SM_EXPECT = sm_f(SM_INPUT)

TRANS_PLAIN = "secret_message"
TRANS_CIPHER = transform_encode(TRANS_PLAIN)

JS_FLAG = "FLAG{js_vm_ok}"
JS_PROG = build_stack_prog(JS_FLAG, 0x33)

PRE_X0 = 4242
PRE_TARGET = pre_g(PRE_X0)

AUTH_PW0 = "mint"
AUTH_STORED = auth_H(AUTH_PW0)

TASKS = []

# 1. stack VM -> recover printed flag ------------------------------------
TASKS.append(T("re_vm_stack_flag", "vm",
setup=f"""cat > vm.py <<'EOF'
# tiny stack VM. PROG is a flat int stream of opcodes+operands.
# 0 PUSH imm | 1 ADD | 2 XOR | 3 EMIT(pop->char) | 4 DUP | 5 MUL | 6 HALT
PROG = {STACK_PROG}
def run(prog):
    st = []; out = []; i = 0
    while i < len(prog):
        op = prog[i]; i += 1
        if op == 0:
            st.append(prog[i]); i += 1
        elif op == 1:
            b = st.pop(); a = st.pop(); st.append((a + b) & 0xff)
        elif op == 2:
            b = st.pop(); a = st.pop(); st.append(a ^ b)
        elif op == 3:
            out.append(chr(st.pop()))
        elif op == 4:
            st.append(st[-1])
        elif op == 5:
            b = st.pop(); a = st.pop(); st.append((a * b) & 0xff)
        elif op == 6:
            break
    return ''.join(out)

if __name__ == '__main__':
    print(run(PROG))
EOF
""",
prompt="vm.py contains a stack-machine bytecode program (PROG) and its interpreter. Trace or run it to recover the flag string it builds and prints. Write the exact flag (no trailing newline) to answer.txt in the working directory.",
grader="""python3 - <<'PY'
try: a = open('answer.txt').read().strip()
except Exception: a = ''
exp = 'FLAG{st4ck_trace}'
print('PASS' if a == exp else 'FAIL: ' + repr(a))
PY""",
reference="python3 vm.py | tr -d '\\n' > answer.txt\n"))

# 2. register VM -> recover printed value --------------------------------
TASKS.append(T("re_vm_reg_output", "vm",
setup=f"""cat > reg.py <<'EOF'
# register VM: r[0..3], r[0] preloaded with INPUT. Program prints one int at OUT.
# ins tuple (op,a,b): 0 SETI r=a v=b | 1 ADD r=a += r[b] | 2 XORI r=a ^= b
#   3 MULI r=a *= b | 4 SHLI r=a <<= b | 5 ANDI r=a &= b | 6 MOV r=a = r[b]
#   7 OUT r=a | 8 HALT     (all arithmetic mod 2**32)
INPUT = {REG_INPUT}
PROG = {REG_PROG}
def run(x):
    r = [x, 0, 0, 0]; out = None
    for ins in PROG:
        op = ins[0]
        if op == 0: r[ins[1]] = ins[2]
        elif op == 1: r[ins[1]] = (r[ins[1]] + r[ins[2]]) & 0xffffffff
        elif op == 2: r[ins[1]] = r[ins[1]] ^ ins[2]
        elif op == 3: r[ins[1]] = (r[ins[1]] * ins[2]) & 0xffffffff
        elif op == 4: r[ins[1]] = (r[ins[1]] << ins[2]) & 0xffffffff
        elif op == 5: r[ins[1]] = r[ins[1]] & ins[2]
        elif op == 6: r[ins[1]] = r[ins[2]]
        elif op == 7: out = r[ins[1]]
        elif op == 8: break
    return out

if __name__ == '__main__':
    print(run(INPUT))
EOF
""",
prompt="reg.py is a small register-VM program run on the baked INPUT; it prints a single integer. Recover that integer and write it (decimal) to answer.txt.",
grader=f"""python3 - <<'PY'
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if a == '{REG_EXPECT}' else 'FAIL: ' + repr(a))
PY""",
reference="python3 reg.py | tr -d '\\n' > answer.txt\n"))

# 3. serial validator -> find any accepting serial -----------------------
TASKS.append(T("re_serial_accept", "serial",
setup="""cat > check.py <<'EOF'
def valid(s):
    if len(s) != 5 or not s.isdigit():
        return False
    d = [int(c) for c in s]
    if d[0] + d[4] != 9: return False
    if d[1] * d[2] != 12: return False
    if (d[3] ^ d[0]) != 5: return False
    if sum(d) != 20: return False
    return True
EOF
""",
prompt="check.py exposes valid(s), a serial validator over 5-digit strings. Find any serial it accepts and write it to answer.txt. The grader re-runs valid() on your serial.",
grader="""python3 - <<'PY'
import check
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if check.valid(a) else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
import check
for n in range(100000):
    s = "%05d" % n
    if check.valid(s):
        open('answer.txt', 'w').write(s); break
PY
"""))

# 4. keygen reversal -----------------------------------------------------
TASKS.append(T("re_keygen", "keygen",
setup="""cat > lic.py <<'EOF'
def _h(name):
    h = 0
    for c in name:
        h = (h * 31 + ord(c)) & 0xffffffff
    return h

def validate(name, serial):
    h = _h(name)
    expect = "%04X-%04X" % ((h >> 16) & 0xffff, h & 0xffff)
    return serial == expect
EOF
""",
prompt="lic.py validate(name, serial) accepts a serial derived from name. For the name \"acme-corp\", produce a serial validate() accepts and write it to answer.txt. The grader runs validate(\"acme-corp\", your_answer).",
grader="""python3 - <<'PY'
import lic
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if lic.validate("acme-corp", a) else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
import lic
h = lic._h("acme-corp")
open('answer.txt', 'w').write("%04X-%04X" % ((h >> 16) & 0xffff, h & 0xffff))
PY
"""))

# 5. patch the check -----------------------------------------------------
TASKS.append(T("re_patch_check", "patch",
setup="""cat > locked.py <<'EOF'
def unlock():
    key = 0
    magic = 0x539
    if key == magic:
        return "GRANTED"
    return "DENIED"
EOF
""",
prompt="locked.py unlock() currently returns \"DENIED\". Edit exactly one line in locked.py in place so unlock() returns \"GRANTED\". The grader imports locked and calls unlock().",
grader="""out=$(python3 -c 'import locked; print(locked.unlock())' 2>/dev/null)
test "$out" = GRANTED && echo PASS || echo FAIL""",
reference="""python3 - <<'PY'
src = open('locked.py').read().replace('key = 0\\n', 'key = 0x539\\n', 1)
open('locked.py', 'w').write(src)
PY
"""))

# 6. constraint solve 6-char key -----------------------------------------
TASKS.append(T("re_constraint_key", "constraint",
setup="""cat > constraints.py <<'EOF'
def check(k):
    if len(k) != 6 or not all('a' <= c <= 'z' for c in k):
        return False
    o = [ord(c) for c in k]
    return (o[0] + o[1] == 229 and o[0] - o[1] == -5 and
            o[2] + o[3] == 244 and o[2] - o[3] == 0 and
            o[4] + o[5] == 209 and o[4] - o[5] == 7)
EOF
""",
prompt="constraints.py check(k) accepts one 6-char lowercase key satisfying a set of char-code equations. Recover the key and write it to answer.txt. The grader runs check() on it.",
grader="""python3 - <<'PY'
import constraints
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if constraints.check(a) else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
def pair(su, di):
    return chr((su + di) // 2) + chr((su - di) // 2)
k = pair(229, -5) + pair(244, 0) + pair(209, 7)
open('answer.txt', 'w').write(k)
PY
"""))

# 7. flattened state machine ---------------------------------------------
TASKS.append(T("re_flatten_sm", "obfuscation",
setup=f"""cat > sm.py <<'EOF'
# flattened control flow: one dispatch loop over `state`.
def f(n):
    state = 0; acc = 0; x = n
    while True:
        if state == 0:
            acc = x + 7; state = 3
        elif state == 3:
            acc = acc ^ 0x2a; state = 1
        elif state == 1:
            state = 2 if acc % 2 == 0 else 4
        elif state == 2:
            acc = (acc * 3) & 0xffff; state = 5
        elif state == 4:
            acc = (acc + 100) & 0xffff; state = 5
        elif state == 5:
            return acc

INPUT = {SM_INPUT}
if __name__ == '__main__':
    print(f(INPUT))
EOF
""",
prompt="sm.py f(n) is a flattened state machine (a while loop dispatching on `state`). Recover the exact value it returns for INPUT and write it (decimal) to answer.txt.",
grader=f"""python3 - <<'PY'
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if a == '{SM_EXPECT}' else 'FAIL: ' + repr(a))
PY""",
reference="python3 sm.py | tr -d '\\n' > answer.txt\n"))

# 8. homemade transform -> recover plaintext -----------------------------
TASKS.append(T("re_transform_decode", "transform",
setup=f"""cat > enc.py <<'EOF'
def encode(s):
    out = []
    for i, c in enumerate(s):
        v = (ord(c) + (i * 7) % 26) & 0xff
        v ^= 0x3c
        out.append("%02x" % v)
    return "".join(out)

if __name__ == '__main__':
    import sys
    print(encode(sys.argv[1]))
EOF
printf '{TRANS_CIPHER}' > cipher.txt
""",
prompt="enc.py encode() produced cipher.txt (hex). Invert the transform to recover the original plaintext and write it to answer.txt.",
grader=f"""python3 - <<'PY'
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if a == '{TRANS_PLAIN}' else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
h = open('cipher.txt').read().strip()
bs = [int(h[i:i+2], 16) for i in range(0, len(h), 2)]
out = []
for i, t in enumerate(bs):
    u = t ^ 0x3c
    c = (u - (i * 7) % 26) & 0xff
    out.append(chr(c))
open('answer.txt', 'w').write(''.join(out))
PY
"""))

# 9. JS stack VM -> recover printed flag ---------------------------------
TASKS.append(T("re_js_vm_flag", "vm",
setup=f"""cat > vm.js <<'EOF'
// stack VM. PROG is a flat int stream.
// 0 PUSH imm | 1 ADD | 2 XOR | 3 EMIT | 6 HALT
const PROG = {JS_PROG};
function run(p) {{
  let st = [], out = [], i = 0;
  while (i < p.length) {{
    let op = p[i++];
    if (op === 0) {{ st.push(p[i++]); }}
    else if (op === 1) {{ let b = st.pop(), a = st.pop(); st.push((a + b) & 0xff); }}
    else if (op === 2) {{ let b = st.pop(), a = st.pop(); st.push(a ^ b); }}
    else if (op === 3) {{ out.push(String.fromCharCode(st.pop())); }}
    else if (op === 6) {{ break; }}
  }}
  return out.join('');
}}
console.log(run(PROG));
EOF
""",
prompt="vm.js is a stack-machine bytecode program with its interpreter (run with node). Recover the flag it prints and write it (no trailing newline) to answer.txt.",
grader=f"""python3 - <<'PY'
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if a == '{JS_FLAG}' else 'FAIL: ' + repr(a))
PY""",
reference="node vm.js | tr -d '\\n' > answer.txt\n"))

# 10. JS serial validator -> find accepting serial -----------------------
TASKS.append(T("re_js_serial", "serial",
setup="""cat > check.js <<'EOF'
function valid(s) {
  if (!/^[0-9]{4}$/.test(s)) return false;
  const d = [...s].map(Number);
  if (d[0] + d[1] + d[2] + d[3] !== 20) return false;
  if (d[0] !== d[3]) return false;
  if (d[1] * d[2] !== 15) return false;
  return true;
}
const s = process.argv[2] || "";
console.log(valid(s) ? "OK" : "NO");
EOF
""",
prompt="check.js prints OK when given (as argv) a 4-digit serial it accepts, else NO. Find a serial it accepts and write it to answer.txt. The grader runs: node check.js \"$(cat answer.txt)\".",
grader="""out=$(node check.js "$(cat answer.txt 2>/dev/null)" 2>/dev/null)
test "$out" = OK && echo PASS || echo FAIL""",
reference="""node -e 'const re=/^[0-9]{4}$/; function valid(s){ if(!re.test(s))return false; const d=[...s].map(Number); if(d[0]+d[1]+d[2]+d[3]!==20)return false; if(d[0]!==d[3])return false; if(d[1]*d[2]!==15)return false; return true;} const fs=require("fs"); for(let n=0;n<10000;n++){const s=String(n).padStart(4,"0"); if(valid(s)){fs.writeFileSync("answer.txt",s);break;}}'
"""))

# 11. find preimage of obfuscated g --------------------------------------
TASKS.append(T("re_find_preimage", "constraint",
setup=f"""cat > pre.py <<'EOF'
def g(x):
    y = x
    y = (y * 1103515245 + 12345) & 0x7fffffff
    y ^= (y >> 7)
    y = (y * 2654435761) & 0xffffffff
    y ^= (y >> 11)
    return y & 0xffffff

TARGET = {PRE_TARGET}
EOF
""",
prompt="pre.py defines a scrambling function g(x) and a TARGET. Find an integer x in [0, 9999] with g(x) == TARGET and write x (decimal) to answer.txt. The grader recomputes g(x).",
grader="""python3 - <<'PY'
import pre
try:
    a = open('answer.txt').read().strip(); v = int(a)
except Exception:
    print('FAIL: no int'); raise SystemExit
ok = (0 <= v <= 9999 and pre.g(v) == pre.TARGET)
print('PASS' if ok else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
import pre
for x in range(10000):
    if pre.g(x) == pre.TARGET:
        open('answer.txt', 'w').write(str(x)); break
PY
"""))

# 12. crackme: find a preimage of a homemade hash ------------------------
TASKS.append(T("re_hash_crack", "crackme",
setup=f"""cat > auth.py <<'EOF'
def H(pw):
    h = 0x1505
    for c in pw:
        h = ((h << 5) + h + ord(c)) & 0xffffffff
    h ^= (h >> 13)
    return h & 0xffffff

STORED = {AUTH_STORED}
EOF
""",
prompt="auth.py hashes a password with H() and stores STORED = H(secret) for a 4-letter lowercase password. Find any password whose H() equals STORED and write it to answer.txt. The grader recomputes H(answer).",
grader="""python3 - <<'PY'
import auth
try: a = open('answer.txt').read().strip()
except Exception: a = ''
print('PASS' if auth.H(a) == auth.STORED else 'FAIL: ' + repr(a))
PY""",
reference="""python3 - <<'PY'
import auth, itertools, string
for t in itertools.product(string.ascii_lowercase, repeat=4):
    pw = ''.join(t)
    if auth.H(pw) == auth.STORED:
        open('answer.txt', 'w').write(pw); break
PY
"""))

# ---- self-test harness ---------------------------------------------------
def run_sh(script, workdir, cwd=None):
    p = os.path.join(workdir, "_s.sh")
    with open(p, "w") as f:
        f.write(script)
    env = dict(os.environ, W=workdir)
    return subprocess.run(["bash", p], cwd=cwd or workdir, env=env,
                          capture_output=True, text=True, timeout=60)

def graded_pass(out):
    return any(line.strip() == "PASS" for line in out.splitlines())

def validate(task):
    diag = {}
    # phase A: setup -> grader must NOT pass
    dA = tempfile.mkdtemp(prefix="reA_", dir=SCRATCH)
    try:
        run_sh(task["setup_sh"], dA)
        gA = run_sh(task["grader_sh"], dA)
        preA = graded_pass(gA.stdout)
        diag["pre_grader_out"] = gA.stdout.strip()
        diag["pre_grader_err"] = gA.stderr.strip()[:300]
    finally:
        shutil.rmtree(dA, ignore_errors=True)
    # phase B: setup -> reference -> grader must PASS
    dB = tempfile.mkdtemp(prefix="reB_", dir=SCRATCH)
    try:
        sB = run_sh(task["setup_sh"], dB)
        rB = run_sh(task["reference_solution_sh"], dB)
        gB = run_sh(task["grader_sh"], dB)
        postB = graded_pass(gB.stdout)
        diag["setup_err"] = sB.stderr.strip()[:300]
        diag["ref_err"] = rB.stderr.strip()[:300]
        diag["post_grader_out"] = gB.stdout.strip()
        diag["post_grader_err"] = gB.stderr.strip()[:300]
    finally:
        shutil.rmtree(dB, ignore_errors=True)
    ok = (not preA) and postB
    return ok, diag

def main():
    kept = []
    print("id                        preFAIL  postPASS  verdict")
    print("-" * 60)
    for t in TASKS:
        ok, diag = validate(t)
        pre_ok = "PASS" not in diag.get("pre_grader_out", "")  # informational
        preFAIL = not graded_pass(diag.get("pre_grader_out", ""))
        postPASS = graded_pass(diag.get("post_grader_out", ""))
        verdict = "KEEP" if ok else "DROP"
        print(f"{t['id']:<25} {str(preFAIL):<8} {str(postPASS):<9} {verdict}")
        if not ok:
            print("   diag:", json.dumps(diag)[:500])
        if ok:
            t["self_test_passed"] = True
            kept.append(t)
    with open(OUT, "w") as f:
        json.dump({"tasks": kept}, f, indent=1)
    print("-" * 60)
    print(f"validated {len(kept)}/{len(TASKS)} -> {OUT}")

if __name__ == "__main__":
    main()
