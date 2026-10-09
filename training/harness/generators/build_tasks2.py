#!/usr/bin/env python3
"""Author + self-test sysadmin/shell/data-wrangling gradeable tasks.

Validation contract per task:
  setup -> grader            : must NOT print PASS
  setup -> reference -> grader: must print PASS
Only passing tasks are emitted with self_test_passed=true.
"""
import json, os, subprocess, tempfile, shutil, sys

SETUP_PRE  = '#!/usr/bin/env bash\nset -e\ncd "$W" || exit 1\n'
GRADER_PRE = '#!/usr/bin/env bash\ncd "$W" || exit 1\n'
REF_PRE    = '#!/usr/bin/env bash\nset -e\ncd "$W" || exit 1\n'

def T(id, category, setup, prompt, grader, ref):
    return dict(id=id, category=category,
                setup_sh=SETUP_PRE + setup,
                prompt=prompt,
                grader_sh=GRADER_PRE + grader,
                reference_solution_sh=REF_PRE + ref,
                self_test_passed=False)

TASKS = []

# ------------------------------------------------------------------ 1. p95 latency
TASKS.append(T("sa_log_p95_latency", "log-stat",
r'''python3 - <<'PY'
lat=[12,45,7,130,22,88,5,260,19,33,410,77,61,150,9,240,3,95,510,180]
with open('access.log','w') as f:
    for i,l in enumerate(lat):
        f.write(f"10.0.0.{i%8} - GET /api/x 200 {l}ms\n")
PY
''',
"access.log has one request per line; each line ends with a response time token like `45ms`. "
"Compute the p95 response time in milliseconds using the nearest-rank method: sort all response "
"times ascending, then take the value at position ceil(0.95 * N) counting from 1, where N is the "
"number of lines. Write just that integer (no 'ms') to answer.txt.",
r'''python3 - <<'PY'
import math,os
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
lat=[]
for line in open('access.log'):
    line=line.strip()
    if not line: continue
    lat.append(int(line.split()[-1][:-2]))
lat.sort(); N=len(lat)
exp=lat[math.ceil(0.95*N)-1]
try: got=int(open('answer.txt').read().strip())
except Exception: fail()
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import math
lat=[int(l.split()[-1][:-2]) for l in open('access.log') if l.strip()]
lat.sort(); N=len(lat)
open('answer.txt','w').write(str(lat[math.ceil(0.95*N)-1])+'\n')
PY
'''))

# ------------------------------------------------------------------ 2. top 5xx endpoint
TASKS.append(T("sa_log_top5xx_endpoint", "log-stat",
r'''cat > access.log <<'EOF'
10.0.0.1 - - [01/Jun/2024:10:00:01 +0000] "GET /api/a HTTP/1.1" 500 120
10.0.0.2 - - [01/Jun/2024:10:00:02 +0000] "GET /api/a HTTP/1.1" 502 80
10.0.0.3 - - [01/Jun/2024:10:00:03 +0000] "POST /api/b HTTP/1.1" 503 10
10.0.0.4 - - [01/Jun/2024:10:00:04 +0000] "GET /api/b HTTP/1.1" 500 33
10.0.0.5 - - [01/Jun/2024:10:00:05 +0000] "GET /api/b HTTP/1.1" 500 44
10.0.0.6 - - [01/Jun/2024:10:00:06 +0000] "GET /api/c HTTP/1.1" 404 12
10.0.0.7 - - [01/Jun/2024:10:00:07 +0000] "GET /api/c HTTP/1.1" 500 55
10.0.0.8 - - [01/Jun/2024:10:00:08 +0000] "GET /api/a HTTP/1.1" 200 66
10.0.0.9 - - [01/Jun/2024:10:00:09 +0000] "GET /api/b HTTP/1.1" 200 77
10.0.0.1 - - [01/Jun/2024:10:00:10 +0000] "DELETE /api/d HTTP/1.1" 500 88
10.0.0.2 - - [01/Jun/2024:10:00:11 +0000] "GET /api/d HTTP/1.1" 301 99
EOF
''',
"access.log is in the common/combined access-log format. Among requests whose HTTP status code is "
">= 500, find the request path (the path token inside the quoted request, e.g. `/api/b`) that "
"occurs most often. There is a unique winner. Write that path to answer.txt.",
r'''python3 - <<'PY'
import re,os,collections
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
c=collections.Counter()
for line in open('access.log'):
    m=re.search(r'"[A-Z]+ (\S+) HTTP/[0-9.]+" (\d{3})',line)
    if not m: continue
    path,st=m.group(1),int(m.group(2))
    if st>=500: c[path]+=1
exp=c.most_common(1)[0][0]
got=open('answer.txt').read().strip()
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import re,collections
c=collections.Counter()
for line in open('access.log'):
    m=re.search(r'"[A-Z]+ (\S+) HTTP/[0-9.]+" (\d{3})',line)
    if m and int(m.group(2))>=500: c[m.group(1)]+=1
open('answer.txt','w').write(c.most_common(1)[0][0]+'\n')
PY
'''))

# ------------------------------------------------------------------ 3. unique IPs in window
TASKS.append(T("sa_log_unique_ips_window", "log-stat",
r'''cat > req.log <<'EOF'
2024-10-10T13:00:00 1.1.1.1 GET /
2024-10-10T13:05:00 1.1.1.1 GET /a
2024-10-10T13:29:59 2.2.2.2 GET /b
2024-10-10T13:30:00 3.3.3.3 GET /c
2024-10-10T13:30:01 4.4.4.4 GET /d
2024-10-10T12:59:59 5.5.5.5 GET /e
2024-10-10T13:15:00 2.2.2.2 GET /f
2024-10-10T13:20:00 6.6.6.6 GET /g
2024-10-10T14:00:00 7.7.7.7 GET /h
EOF
''',
"req.log lines are `<ISO8601-timestamp> <IP> <method> <path>`. Count the number of DISTINCT IP "
"addresses whose timestamp falls within the inclusive window 2024-10-10T13:00:00 to "
"2024-10-10T13:30:00. Write that integer to answer.txt.",
r'''python3 - <<'PY'
import os
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
lo,hi='2024-10-10T13:00:00','2024-10-10T13:30:00'
ips=set()
for line in open('req.log'):
    p=line.split()
    if len(p)<2: continue
    if lo<=p[0]<=hi: ips.add(p[1])
try: got=int(open('answer.txt').read().strip())
except Exception: fail()
print('PASS' if got==len(ips) else 'FAIL')
PY
''',
r'''python3 - <<'PY'
lo,hi='2024-10-10T13:00:00','2024-10-10T13:30:00'
ips={l.split()[1] for l in open('req.log') if l.split() and lo<=l.split()[0]<=hi}
open('answer.txt','w').write(str(len(ips))+'\n')
PY
'''))

# ------------------------------------------------------------------ 4. csv -> json
TASKS.append(T("sa_csv_to_json", "reshape",
r'''cat > users.csv <<'EOF'
id,name,age,active
3,Charlie,30,true
1,Alice,25,true
2,Bob,41,false
EOF
''',
"Convert users.csv to a JSON array of objects and write it to answer.json. Rules: one object per "
"data row with keys id, name, age, active; `id` and `age` must be JSON numbers (integers); "
"`active` must be a JSON boolean (true/false); `name` stays a string. Sort the array by id "
"ascending.",
r'''python3 - <<'PY'
import json,os,csv
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.json'): fail()
rows=list(csv.DictReader(open('users.csv')))
exp=sorted([{'id':int(r['id']),'name':r['name'],'age':int(r['age']),
             'active':r['active'].strip().lower()=='true'} for r in rows],
           key=lambda d:d['id'])
try: got=json.load(open('answer.json'))
except Exception: fail()
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import json,csv
rows=list(csv.DictReader(open('users.csv')))
out=sorted([{'id':int(r['id']),'name':r['name'],'age':int(r['age']),
             'active':r['active'].strip().lower()=='true'} for r in rows],
           key=lambda d:d['id'])
json.dump(out,open('answer.json','w'),indent=2)
PY
'''))

# ------------------------------------------------------------------ 5. join two files
TASKS.append(T("sa_join_orders_users", "join",
r'''cat > users.csv <<'EOF'
user_id,name
u1,Alice
u2,Bob
u3,Carol
EOF
cat > orders.csv <<'EOF'
order_id,user_id,amount
o3,u2,30.00
o1,u1,10.50
o2,u1,5.25
o4,u3,100.00
EOF
''',
"Inner-join orders.csv and users.csv on user_id. Write answer.csv with header line "
"`order_id,name,amount` followed by one row per order containing the order_id, the matching "
"user's name, and the amount (unchanged). Sort the data rows by order_id ascending.",
r'''python3 - <<'PY'
import os,csv
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.csv'): fail()
u={r['user_id']:r['name'] for r in csv.DictReader(open('users.csv'))}
o=list(csv.DictReader(open('orders.csv')))
rows=sorted([(r['order_id'],u[r['user_id']],r['amount']) for r in o if r['user_id'] in u],
            key=lambda t:t[0])
exp=['order_id,name,amount']+[','.join(t) for t in rows]
got=[l.rstrip('\n') for l in open('answer.csv') if l.strip()!='']
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import csv
u={r['user_id']:r['name'] for r in csv.DictReader(open('users.csv'))}
o=list(csv.DictReader(open('orders.csv')))
rows=sorted([(r['order_id'],u[r['user_id']],r['amount']) for r in o if r['user_id'] in u],
            key=lambda t:t[0])
with open('answer.csv','w') as f:
    f.write('order_id,name,amount\n')
    for t in rows: f.write(','.join(t)+'\n')
PY
'''))

# ------------------------------------------------------------------ 6. ipv4 extract sorted
TASKS.append(T("sa_regex_ipv4_sorted", "regex",
r'''cat > text.txt <<'EOF'
Server 10.0.0.1 responded; gateway=192.168.1.1, bad=256.1.1.1
loopback 127.0.0.1 and 10.0.0.1 again. Not an ip: 999.999.999.999 or 1.2.3
edge 8.8.8.8, 172.16.254.1 end.
EOF
''',
"Extract every valid IPv4 address from text.txt. Valid means four dot-separated octets, each an "
"integer 0-255 (reject things like 256.1.1.1 or 999.999.999.999, and 1.2.3 which has only three). "
"Write the UNIQUE addresses to answer.txt, one per line, sorted in ascending numeric order by "
"octet (compare as the 4-integer tuple).",
r'''python3 - <<'PY'
import re,os
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
t=open('text.txt').read()
cand=re.findall(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])',t)
ok=sorted({c for c in cand if all(0<=int(o)<=255 for o in c.split('.'))},
          key=lambda ip:tuple(int(o) for o in ip.split('.')))
got=[l.rstrip('\n') for l in open('answer.txt') if l.strip()!='']
print('PASS' if got==ok else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import re
t=open('text.txt').read()
cand=re.findall(r'(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])',t)
ok=sorted({c for c in cand if all(0<=int(o)<=255 for o in c.split('.'))},
          key=lambda ip:tuple(int(o) for o in ip.split('.')))
open('answer.txt','w').write('\n'.join(ok)+'\n')
PY
'''))

# ------------------------------------------------------------------ 7. timestamps to ISO8601
TASKS.append(T("sa_ts_to_iso8601", "text-transform",
r'''cat > times.txt <<'EOF'
06/01/2024 09:05:03
12/31/2023 23:59:59
01/02/2024 00:00:00
EOF
''',
"times.txt has one timestamp per line in the format MM/DD/YYYY HH:MM:SS (24-hour clock). Rewrite "
"each into ISO 8601 basic form YYYY-MM-DDTHH:MM:SS and write the results to answer.txt in the same "
"order, one per line.",
r'''python3 - <<'PY'
import re,os
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
exp=[]
for l in open('times.txt'):
    l=l.strip()
    if not l: continue
    m=re.match(r'(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})$',l)
    if not m: fail()
    mo,d,y,H,M,S=m.groups()
    exp.append(f"{y}-{mo}-{d}T{H}:{M}:{S}")
got=[l.rstrip('\n') for l in open('answer.txt') if l.strip()!='']
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import re
out=[]
for l in open('times.txt'):
    l=l.strip()
    if not l: continue
    mo,d,y,H,M,S=re.match(r'(\d{2})/(\d{2})/(\d{4}) (\d{2}):(\d{2}):(\d{2})$',l).groups()
    out.append(f"{y}-{mo}-{d}T{H}:{M}:{S}")
open('answer.txt','w').write('\n'.join(out)+'\n')
PY
'''))

# ------------------------------------------------------------------ 8. redact card column
TASKS.append(T("sa_redact_card_column", "text-transform",
r'''cat > payments.csv <<'EOF'
id,name,card,amount
1,Alice,4111111111111111,10.00
2,Bob,5500005555555559,20.00
3,Carol,340000000000009,30.00
EOF
''',
"payments.csv has a `card` column. Write answer.csv identical to payments.csv except that every "
"value in the `card` column has all characters replaced by `*` EXCEPT the last 4, which are kept "
"(preserve the original length). Header and all other columns are unchanged.",
r'''python3 - <<'PY'
import os,csv,io
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.csv'): fail()
def mask(c): return '*'*(len(c)-4)+c[-4:] if len(c)>4 else c
rd=list(csv.reader(open('payments.csv')))
hdr=rd[0]; ci=hdr.index('card')
exp=[hdr]+[ [ (mask(v) if j==ci else v) for j,v in enumerate(row)] for row in rd[1:]]
got=list(csv.reader(open('answer.csv')))
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import csv
def mask(c): return '*'*(len(c)-4)+c[-4:] if len(c)>4 else c
rd=list(csv.reader(open('payments.csv')))
hdr=rd[0]; ci=hdr.index('card')
with open('answer.csv','w',newline='') as f:
    w=csv.writer(f); w.writerow(hdr)
    for row in rd[1:]:
        w.writerow([ (mask(v) if j==ci else v) for j,v in enumerate(row)])
PY
'''))

# ------------------------------------------------------------------ 9. fix json config
TASKS.append(T("sa_fix_json_config", "config-fix",
r'''cat > config.json <<'EOF'
{
  "service": "api",
  "port": 80,
  "tls": {
    "enabled": false,
  },
  "workers": 4
}
EOF
''',
"config.json is not valid JSON (there is a trailing comma) and has wrong values. Fix it IN PLACE "
"so that: it parses as valid JSON, `port` equals 8080, and `tls.enabled` is the boolean true. "
"Keep the other keys (service, workers) intact.",
r'''python3 - <<'PY'
import json
def fail(): print('FAIL'); raise SystemExit
try: d=json.load(open('config.json'))
except Exception: fail()
ok=(d.get('port')==8080 and d.get('tls',{}).get('enabled') is True
    and d.get('service')=='api' and d.get('workers')==4)
print('PASS' if ok else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import json
d={"service":"api","port":8080,"tls":{"enabled":True},"workers":4}
json.dump(d,open('config.json','w'),indent=2)
PY
'''))

# ------------------------------------------------------------------ 10. fix crontab
TASKS.append(T("sa_fix_crontab", "config-fix",
r'''cat > crontab <<'EOF'
# m h dom mon dow  command
0 0 * * * /usr/bin/rotate-logs
99 2 * * * /usr/bin/backup
*/15 * * * * /usr/bin/healthcheck
EOF
''',
"The file `crontab` has a broken entry: the /usr/bin/backup job uses minute field 99, which is out "
"of range. Fix crontab IN PLACE so that every schedule uses valid fields (minute 0-59, hour 0-23, "
"day-of-month 1-31, month 1-12, day-of-week 0-7) AND the /usr/bin/backup job runs daily at 02:30 "
"(schedule `30 2 * * *`). Leave the other two jobs working.",
r'''python3 - <<'PY'
def vf(tok,lo,hi):
    for part in tok.split(','):
        if part=='*': continue
        if part.startswith('*/'):
            s=part[2:]
            if not (s.isdigit() and int(s)>0): return False
            continue
        if '/' in part:
            base,step=part.split('/',1)
            if not (step.isdigit() and int(step)>0): return False
            part=base
            if part=='*': continue
        if '-' in part:
            a,b=part.split('-',1)
            if not (a.isdigit() and b.isdigit()): return False
            a,b=int(a),int(b)
            if not (lo<=a<=hi and lo<=b<=hi and a<=b): return False
            continue
        if not part.isdigit(): return False
        if not (lo<=int(part)<=hi): return False
    return True
R=[(0,59),(0,23),(1,31),(1,12),(0,7)]
ok=True; backup=False
for l in open('crontab'):
    s=l.strip()
    if not s or s.startswith('#'): continue
    p=s.split(None,5)
    if len(p)<6: ok=False; break
    fields=p[:5]
    for f,(lo,hi) in zip(fields,R):
        if not vf(f,lo,hi): ok=False
    if fields==['30','2','*','*','*'] and '/usr/bin/backup' in p[5]: backup=True
print('PASS' if ok and backup else 'FAIL')
PY
''',
r'''cat > crontab <<'EOF'
# m h dom mon dow  command
0 0 * * * /usr/bin/rotate-logs
30 2 * * * /usr/bin/backup
*/15 * * * * /usr/bin/healthcheck
EOF
'''))

# ------------------------------------------------------------------ 11. unique .txt content count
TASKS.append(T("sa_unique_content_count", "checksum-rollup",
r'''mkdir -p tree/sub
printf 'hello\n' > tree/a.txt
printf 'world\n' > tree/b.txt
printf 'hello\n' > tree/sub/c.txt
printf 'unique\n' > tree/sub/d.txt
printf 'hello\n' > tree/e.log
''',
"Look at every file with a `.txt` extension anywhere under the tree/ directory (recursively). "
"Count how many DISTINCT file contents there are, comparing files by their exact bytes. Files with "
"other extensions are ignored. Write the count to answer.txt.",
r'''python3 - <<'PY'
import os,hashlib
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
seen=set()
for root,_,files in os.walk('tree'):
    for fn in files:
        if fn.endswith('.txt'):
            seen.add(hashlib.sha256(open(os.path.join(root,fn),'rb').read()).hexdigest())
try: got=int(open('answer.txt').read().strip())
except Exception: fail()
print('PASS' if got==len(seen) else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import os,hashlib
seen=set()
for root,_,files in os.walk('tree'):
    for fn in files:
        if fn.endswith('.txt'):
            seen.add(hashlib.sha256(open(os.path.join(root,fn),'rb').read()).hexdigest())
open('answer.txt','w').write(str(len(seen))+'\n')
PY
'''))

# ------------------------------------------------------------------ 12. word freq top5 pipeline
TASKS.append(T("sa_word_freq_top5", "pipeline",
r'''cat > input.txt <<'EOF'
the quick brown fox
The QUICK brown dog
the lazy Dog jumps
brown FOX brown
EOF
''',
"Count word frequencies in input.txt, case-insensitively, splitting on whitespace. Emit the top 5 "
"words ordered by count descending, ties broken by the word ascending (alphabetical). Write one "
"line per word as `<count> <word>` (word in lowercase) to answer.txt.",
r'''python3 - <<'PY'
import os,collections
def fail(): print('FAIL'); raise SystemExit
if not os.path.exists('answer.txt'): fail()
c=collections.Counter(open('input.txt').read().lower().split())
order=sorted(c.items(),key=lambda kv:(-kv[1],kv[0]))[:5]
exp=[f"{n} {w}" for w,n in order]
got=[l.rstrip('\n') for l in open('answer.txt') if l.strip()!='']
print('PASS' if got==exp else 'FAIL')
PY
''',
r'''python3 - <<'PY'
import collections
c=collections.Counter(open('input.txt').read().lower().split())
order=sorted(c.items(),key=lambda kv:(-kv[1],kv[0]))[:5]
open('answer.txt','w').write('\n'.join(f"{n} {w}" for w,n in order)+'\n')
PY
'''))

# ================================================================== self-test harness
def run(script, W):
    return subprocess.run(['bash','-c',script], env={**os.environ,'W':W},
                          capture_output=True, text=True, timeout=60)

def last_word(s):
    toks=s.split()
    return toks[-1] if toks else ''

def selftest(task):
    # phase A: setup -> grader must NOT be PASS
    Wa=tempfile.mkdtemp(prefix='w_')
    try:
        r=run(task['setup_sh'],Wa)
        if r.returncode!=0:
            return False, f"setup(A) rc={r.returncode}: {r.stderr.strip()[:200]}"
        g=run(task['grader_sh'],Wa)
        ga=last_word(g.stdout)
        if ga=='PASS':
            return False, "grader PASSed on bare setup (should FAIL)"
    finally:
        shutil.rmtree(Wa,ignore_errors=True)
    # phase B: setup -> reference -> grader must be PASS
    Wb=tempfile.mkdtemp(prefix='w_')
    try:
        r=run(task['setup_sh'],Wb)
        if r.returncode!=0:
            return False, f"setup(B) rc={r.returncode}: {r.stderr.strip()[:200]}"
        ref=run(task['reference_solution_sh'],Wb)
        if ref.returncode!=0:
            return False, f"reference rc={ref.returncode}: {ref.stderr.strip()[:200]}"
        g=run(task['grader_sh'],Wb)
        gb=last_word(g.stdout)
        if gb!='PASS':
            return False, f"grader after reference != PASS (got {gb!r}); stderr={g.stderr.strip()[:200]}"
    finally:
        shutil.rmtree(Wb,ignore_errors=True)
    return True, "ok"

kept=[]
for t in TASKS:
    ok,msg=selftest(t)
    t['self_test_passed']=ok
    print(f"[{'PASS' if ok else 'FAIL'}] {t['id']:<28} {msg}", file=sys.stderr)
    if ok: kept.append(t)

out='/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_sysadmin.json'
json.dump({"tasks":kept}, open(out,'w'), indent=1)
print(f"\nvalidated {len(kept)}/{len(TASKS)} -> {out}", file=sys.stderr)
