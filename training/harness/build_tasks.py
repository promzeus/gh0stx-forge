#!/usr/bin/env python3
"""Author + self-test new authored_tasks for gh0stx RFT. Each task is self-contained
(setup builds fixtures), gradeable (grader checks RESULT.txt), and self-tested here
(setup -> reference_solution -> grader must print PASS). --emit appends PASS tasks to
authored_tasks.json with self_test_passed=true. Sources: standard public methodology
(reverse a checker, ECDSA nonce reuse, single-byte XOR crib), authorized fixtures only."""
import json, os, sys, subprocess, tempfile, shutil

TASKS = [
{
 "id": "sec_crackme_xor",
 "category": "reverse-or-decode",
 "behavior": "recover a checker's expected input by reversing its transform against the running program, not by brute force",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W/check.py" <<'PY'
import sys
PASS="mimicry"; KEY=0x5a
target="".join("%02x"%(ord(c)^KEY) for c in PASS)
open(sys.argv[1],"w").write(
"import sys\n"
"KEY=0x%02x\n"%KEY +
'TARGET="%s"\n'%target +
"def t(s):\n    return ''.join('%02x'%(ord(c)^KEY) for c in s)\n"
"print('ACCESS GRANTED' if len(sys.argv)>1 and t(sys.argv[1])==TARGET else 'denied')\n")
PY
''',
 "prompt": "check.py in the working directory accepts one argument and prints ACCESS GRANTED only for the correct passphrase, otherwise denied. Recover the passphrase by reading check.py and reversing its check; do not brute-force. Write only the passphrase to RESULT.txt, then run python3 check.py \"$(cat RESULT.txt)\" and confirm it prints ACCESS GRANTED.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
out=$(python3 "$W/check.py" "$(tr -d '\n\r' < "$W/RESULT.txt")" 2>/dev/null)
case "$out" in *"ACCESS GRANTED"*) echo PASS;; *) echo "FAIL(out=$out)";; esac
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W/check.py" "$W/RESULT.txt" <<'PY'
import sys,re
src=open(sys.argv[1]).read()
key=int(re.search(r"KEY=0x([0-9a-fA-F]+)",src).group(1),16)
tgt=re.search(r'TARGET="([0-9a-f]+)"',src).group(1)
pw="".join(chr(b^key) for b in bytes.fromhex(tgt))
open(sys.argv[2],"w").write(pw)
PY
''',
 "notes": "crackme: reverse the transform (hex-decode TARGET, XOR KEY)",
},
{
 "id": "sec_ecdsa_nonce_reuse",
 "category": "reverse-or-decode",
 "behavior": "exploit ECDSA nonce reuse: two signatures sharing k recover the private key by modular arithmetic",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W/sigs.txt" <<'PY'
import sys
n=0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
d=(0x1a2b3c4d5e6f77889900112233445566778899aabbccddeeff00112233445566)%n
k=(0x9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08)%n
r=(0xcafebabedeadbeef0123456789abcdef0011223344556677f0e1d2c3b4a59687)%n
z1=(0x53d0e6b1c1b0a9f8e7d6c5b4a3928170ffeeddccbbaa99887766554433221100)%n
z2=(0x0011223344556677889900aabbccddeeff102030405060708090a0b0c0d0e0f0)%n
ki=pow(k,-1,n)
s1=(ki*(z1+r*d))%n; s2=(ki*(z2+r*d))%n
open(sys.argv[1],"w").write(
"n=%x\nr=%x\ns1=%x\ns2=%x\nz1=%x\nz2=%x\n"%(n,r,s1,s2,z1,z2))
PY
''',
 "prompt": "sigs.txt holds a secp256k1 ECDSA setup where two messages were signed with the same nonce k. It lists n, r, s1, s2, z1, z2 as name=hex, one per line. Reuse of k lets you recover the signer's private key d. Compute d and write it as lowercase hex without a 0x prefix to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
python3 - "$W/sigs.txt" "$W/RESULT.txt" <<'PY'
import sys,re
v=dict(re.findall(r"(\w+)=([0-9a-fA-F]+)",open(sys.argv[1]).read()))
n,r,s1,s2,z1,z2=(int(v[x],16) for x in ["n","r","s1","s2","z1","z2"])
k=((z1-z2)*pow((s1-s2)%n,-1,n))%n
d=((s1*k - z1)*pow(r,-1,n))%n
got=open(sys.argv[2]).read().strip().lower().replace("0x","").lstrip("0") or "0"
exp=("%x"%d).lstrip("0") or "0"
print("PASS" if got==exp else "FAIL(exp=%s got=%s)"%(exp,got))
PY
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W/sigs.txt" "$W/RESULT.txt" <<'PY'
import sys,re
v=dict(re.findall(r"(\w+)=([0-9a-fA-F]+)",open(sys.argv[1]).read()))
n,r,s1,s2,z1,z2=(int(v[x],16) for x in ["n","r","s1","s2","z1","z2"])
k=((z1-z2)*pow((s1-s2)%n,-1,n))%n
d=((s1*k - z1)*pow(r,-1,n))%n
open(sys.argv[2],"w").write("%x"%d)
PY
''',
 "notes": "nonce reuse: k=(z1-z2)/(s1-s2), d=(s1*k - z1)/r mod n",
},
{
 "id": "sec_xor_known_plaintext",
 "category": "reverse-or-decode",
 "behavior": "break single-byte XOR with a known-plaintext crib, then read the recovered field",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W/cipher.hex" <<'PY'
import sys
pt=b"FLAG=recovered_by_xor_crib_7f3a"
KEY=0x37
ct=bytes(b^KEY for b in pt)
open(sys.argv[1],"w").write(ct.hex())
PY
''',
 "prompt": "cipher.hex is hex of ASCII plaintext XOR-ed with one unknown byte. The plaintext starts with the crib FLAG=. Recover the key, decrypt, and write only the value after FLAG= (the text following the equals sign) to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
exp=$(python3 - "$W/cipher.hex" <<'PY'
import sys
ct=bytes.fromhex(open(sys.argv[1]).read().strip())
key=ct[0]^ord('F')
pt=bytes(b^key for b in ct).decode('latin1')
print(pt.split('FLAG=',1)[1])
PY
)
got=$(tr -d '\n\r' < "$W/RESULT.txt")
[ "$got" = "$exp" ] && echo PASS || echo "FAIL(exp=$exp got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W/cipher.hex" "$W/RESULT.txt" <<'PY'
import sys
ct=bytes.fromhex(open(sys.argv[1]).read().strip())
key=ct[0]^ord('F')
pt=bytes(b^key for b in ct).decode('latin1')
open(sys.argv[2],"w").write(pt.split('FLAG=',1)[1])
PY
''',
 "notes": "single-byte XOR, crib 'F' recovers key",
},
{
 "id": "sec_go_fix_build",
 "category": "wire-and-run",
 "behavior": "fix a small multi-file Go program so it builds, run it, capture the real output",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
cat > "$W/go.mod" <<'EOF'
module toolx
go 1.21
EOF
cat > "$W/main.go" <<'EOF'
package main

import (
	"fmt"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: toolx <n>")
		return
	}
	n := atoiMust(os.Args[1])
	fmt.Printf("fib=%d\n", fib(n))
}
EOF
cat > "$W/calc.go" <<'EOF'
package main

import "strconv"

// BUG: fib returns wrong base case and atoiMust has a typo'd call.
func fib(n int) int {
	if n < 2 {
		return n
	}
	return fib(n-1) + fib(n-2)
}

func atoiMust(s string) int {
	v, err := strconv.Ato(s)
	if err != nil {
		return 0
	}
	return v
}
EOF
''',
 "prompt": "The Go program in the working directory does not build: calc.go calls strconv.Ato which does not exist. Fix it in place (use the correct strconv function), then run: go build -o toolx . && ./toolx 20 and write the program's exact stdout line to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
got=$(tr -d '\r' < "$W/RESULT.txt" | head -1)
[ "$got" = "fib=6765" ] && echo PASS || echo "FAIL(exp=fib=6765 got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
set -e
sed -i 's/strconv\.Ato(/strconv.Atoi(/' "$W/calc.go"
cd "$W" && GOFLAGS=-mod=mod GOCACHE="$W/.gocache" go build -o toolx . 2>/dev/null
./toolx 20 > "$W/RESULT.txt"
''',
 "notes": "typo strconv.Ato->Atoi; fib(20)=6765",
},
]

def run(script, W, extra_env=None):
    env=dict(os.environ); env["W"]=W
    if extra_env: env.update(extra_env)
    p=subprocess.run(["bash","-c",script], env=env, capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode

def selftest():
    ok=[]
    for t in TASKS:
        W=tempfile.mkdtemp(prefix="stest_")
        try:
            run(t["setup_sh"], W)
            run(t["reference_solution_sh"], W)
            out,err,rc=run(t["grader_sh"], W)
            verdict=out.strip().splitlines()[-1] if out.strip() else "(no grader output)"
            passed=verdict.startswith("PASS")
            print(f"{'PASS' if passed else 'FAIL'}  {t['id']:28s} {verdict if not passed else ''}")
            if not passed and err.strip(): print("      stderr:", err.strip()[:200])
            if passed: ok.append(t["id"])
        finally:
            shutil.rmtree(W, ignore_errors=True)
    print(f"\n{len(ok)}/{len(TASKS)} passed: {ok}")
    return ok

def emit(passed_ids, path):
    d=json.load(open(path)); existing={x["id"] for x in d["tasks"]}
    added=[]
    for t in TASKS:
        if t["id"] in passed_ids and t["id"] not in existing:
            tt=dict(t); tt["self_test_passed"]=True; d["tasks"].append(tt); added.append(t["id"])
    json.dump(d, open(path,"w"), ensure_ascii=False, indent=1)
    print("appended:", added, "| total tasks now:", len(d["tasks"]))

if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--emit":
        ok=selftest()
        emit(ok, sys.argv[2] if len(sys.argv)>2 else "authored_tasks.json")
    else:
        selftest()
