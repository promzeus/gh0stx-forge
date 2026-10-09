#!/usr/bin/env python3
"""Author gradeable NETWORK ATTACK DETECTION & DECODING tasks.

Each task: setup_sh builds a TEXT fixture (packet/flow/DNS/BGP/BPDU dump, one record
per line) mixing an attack with benign traffic. The model detects the attack and writes
extracted specifics to answer.txt. grader_sh recomputes the expected answer directly from
the fixture, parses answer.txt tolerantly, echoes PASS/FAIL. reference_solution_sh derives
and writes the correct answer.

Self-test invariant per task: setup->grader NOT PASS ; setup->reference->grader PASS.
Only passing tasks are emitted with self_test_passed=true.
"""
import json, os, subprocess, tempfile, shutil, sys

SH = "#!/usr/bin/env bash\n"
SETUP = SH + "set -e\n"
CD = SH + 'cd "$W" || exit 1\n'

# ---- tolerant answer.txt parser shared by every grader ----
PARSE = r'''
import re
def _read():
    try:
        return open('answer.txt').read()
    except OSError:
        return ''
def get(key, txt=None):
    if txt is None: txt=_read()
    m=re.search(r'(?im)^\s*'+re.escape(key)+r'\s*[=:]\s*(.+?)\s*$', txt)
    return m.group(1).strip() if m else None
'''

def T(id, category, setup, prompt, grader, reference):
    return dict(id=id, category=category, behavior=category,
                setup_sh=SETUP + setup,
                prompt=prompt,
                grader_sh=CD + grader,
                reference_solution_sh=CD + reference,
                self_test_passed=True,
                notes="network attack detection & decoding")

TASKS = []

# =====================================================================
# Task 1: DNS hex-subdomain tunnel exfil
# =====================================================================
T1_SETUP = r'''python3 - <<'PY'
msg = "SSN=078-05-1120;NAME=J.DOE"
hexs = msg.encode().hex()
base = "t.exfil-c2.net"
chunks = [hexs[i:i+12] for i in range(0, len(hexs), 12)]
exfil = []
for i, c in enumerate(chunks):
    exfil.append(("s%02d.%s.%s" % (i, c, base), "10.0.0.5"))
benign = [
    ("www.google.com","8.8.8.8"),
    ("api.github.com","8.8.8.8"),
    ("cdn.jsdelivr.net","1.1.1.1"),
    ("mail.corp.local","192.168.1.2"),
    ("updates.ubuntu.com","8.8.4.4"),
    ("time.cloudflare.com","1.1.1.1"),
    ("metrics.internal","192.168.1.2"),
]
# interleave: benign, exfil, benign, exfil ...
lines=[]
t=1700000000.000
src="192.168.1.50"
seq_iter=iter(exfil)
bi=0
order=[]
# build an interleaving that scatters exfil among benign
allq=[]
ei=0; bj=0
pattern=[0,1,0,1,1,0,1,1,0,1,1,0,1,1,0,1,1,0,1,1,0,1,1]  # 0=benign 1=exfil
for p in pattern:
    if p==1 and ei<len(exfil):
        allq.append(("A", exfil[ei][0], exfil[ei][1])); ei+=1
    else:
        b=benign[bj%len(benign)]; bj+=1
        allq.append(("A", b[0], b[1]))
# append any leftover exfil
while ei<len(exfil):
    allq.append(("A", exfil[ei][0], exfil[ei][1])); ei+=1
with open("dns_log.txt","w") as f:
    for qt,name,dst in allq:
        f.write("%.3f %s -> %s %s %s\n" % (t, src, dst, qt, name))
        t+=0.417
PY
'''

T1_SOLVE = r'''
import re
lines=open('dns_log.txt').read().splitlines()
pat=re.compile(r'^s(\d+)\.([0-9a-f]+)\.t\.exfil-c2\.net$')
chunks=[]
for ln in lines:
    parts=ln.split()
    if len(parts)<5: continue
    name=parts[-1]
    m=pat.match(name)
    if m:
        chunks.append((int(m.group(1)), m.group(2)))
chunks.sort()
hexs=''.join(c for _,c in chunks)
message=bytes.fromhex(hexs).decode('utf-8','replace')
domain="t.exfil-c2.net"
'''

T1_GRADER = "python3 - <<'PY'\n" + PARSE + T1_SOLVE + r'''
d=get('DOMAIN'); m=get('MESSAGE')
ok = d is not None and d.lower()==domain and m is not None and m.strip()==message
print('PASS' if ok else 'FAIL: domain=%r message=%r (want %r / %r)' % (d,m,domain,message))
PY'''

T1_REF = "python3 - <<'PY'\n" + T1_SOLVE + r'''
open('answer.txt','w').write("DOMAIN=%s\nMESSAGE=%s\n" % (domain, message))
PY'''

TASKS.append(T(
 "net_dns_hex_exfil", "dns-tunnel-decode",
 T1_SETUP,
 "dns_log.txt is a DNS query log, one query per line: '<epoch> <src_ip> -> <resolver_ip> <qtype> <qname>'. "
 "Most queries are benign, but one host is exfiltrating data through a DNS tunnel: a series of queries to subdomains "
 "of a single command-and-control base domain, where each subdomain label carries a sequence number and a hex-encoded "
 "chunk of the payload. Identify the C2 base domain, reassemble the chunks in sequence order, hex-decode them, and "
 "recover the plaintext message. Write your answer to answer.txt with two lines exactly:\n"
 "DOMAIN=<c2 base domain>\nMESSAGE=<decoded plaintext>",
 T1_GRADER, T1_REF))

# =====================================================================
# Task 2: DNS TXT base64 exfil (whole message base64, split across TXT records)
# =====================================================================
T2_SETUP = r'''python3 - <<'PY'
import base64
msg = "AWS_KEY=AKIA7XMPLE;SECRET=abc123XYZ"
b64 = base64.b64encode(msg.encode()).decode()
base = "feed.data-sync.io"
chunks = [b64[i:i+8] for i in range(0, len(b64), 8)]
exfil = [("chunk%02d.%s" % (i, base), c) for i, c in enumerate(chunks)]
benign = [
    ("_dmarc.example.com", "v=DMARC1; p=none"),
    ("example.com", "v=spf1 include:_spf.google.com ~all"),
    ("selector1._domainkey.example.com", "k=rsa; p=MIIBIjANBg"),
    ("status.example.net", "ok"),
]
rows=[]
ei=0; bj=0
pattern=[0,1,1,0,1,1,1,0,1,1,1,0,1,1]
for p in pattern:
    if p==1 and ei<len(exfil):
        rows.append(exfil[ei]); ei+=1
    else:
        rows.append(benign[bj%len(benign)]); bj+=1
while ei<len(exfil):
    rows.append(exfil[ei]); ei+=1
t=1700100000.0
with open("dns_txt.txt","w") as f:
    for name,val in rows:
        f.write('%.3f TXT? %s -> "%s"\n' % (t, name, val))
        t+=0.9
PY
'''

T2_SOLVE = r'''
import re, base64
lines=open('dns_txt.txt').read().splitlines()
pat=re.compile(r'^chunk(\d+)\.feed\.data-sync\.io$')
rowpat=re.compile(r'^\S+ TXT\? (\S+) -> "(.*)"$')
chunks=[]
for ln in lines:
    m=rowpat.match(ln)
    if not m: continue
    name,val=m.group(1),m.group(2)
    mm=pat.match(name)
    if mm:
        chunks.append((int(mm.group(1)), val))
chunks.sort()
b64=''.join(v for _,v in chunks)
message=base64.b64decode(b64).decode('utf-8','replace')
server="feed.data-sync.io"
'''

T2_GRADER = "python3 - <<'PY'\n" + PARSE + T2_SOLVE + r'''
s=get('SERVER'); m=get('MESSAGE')
ok = s is not None and s.lower()==server and m is not None and m.strip()==message
print('PASS' if ok else 'FAIL: server=%r message=%r (want %r / %r)' % (s,m,server,message))
PY'''

T2_REF = "python3 - <<'PY'\n" + T2_SOLVE + r'''
open('answer.txt','w').write("SERVER=%s\nMESSAGE=%s\n" % (server, message))
PY'''

TASKS.append(T(
 "net_dns_txt_b64_exfil", "dns-tunnel-decode",
 T2_SETUP,
 "dns_txt.txt is a log of DNS TXT responses, one per line: '<epoch> TXT? <qname> -> \"<txt_value>\"'. "
 "Benign TXT records (SPF, DMARC, DKIM) are mixed with a DNS TXT exfiltration channel: a base64 blob split into "
 "fixed-size pieces, one piece per TXT record, addressed to numbered subdomains of a single server domain. Find that "
 "server domain, reassemble the base64 pieces in sequence order, base64-decode, and recover the plaintext. "
 "Write answer.txt with exactly:\nSERVER=<server domain>\nMESSAGE=<decoded plaintext>",
 T2_GRADER, T2_REF))

# =====================================================================
# Task 3: NTP exfil (one byte per packet in low byte of transmit timestamp)
# =====================================================================
T3_SETUP = r'''python3 - <<'PY'
msg = "PASSWD:hunter2!"
data = msg.encode()
server = "198.51.100.42"
attacker = "10.0.0.31"
benign_servers = ["216.239.35.0","162.159.200.1","129.6.15.28"]
lines=[]
t=1700200000.0
# benign NTP client queries to real servers
import itertools
bi=0
exfil=[(i,b) for i,b in enumerate(data)]
ei=0
pattern=[0,1,1,0,1,1,1,0,1,1,1,0,1,1,1,0,1,1,1,0,1]
for p in pattern:
    if p==1 and ei<len(exfil):
        seq,byte=exfil[ei]; ei+=1
        lines.append("%.3f %s -> %s mode=3 stratum=0 seq=%d xmt_lsb=0x%02x" % (t, attacker, server, seq, byte))
    else:
        s=benign_servers[bi%len(benign_servers)]; bi+=1
        lines.append("%.3f 192.168.1.%d -> %s mode=3 stratum=2 xmt_lsb=0x%02x" % (t, 10+bi, s, (bi*37)%256))
    t+=1.0
while ei<len(exfil):
    seq,byte=exfil[ei]; ei+=1
    lines.append("%.3f %s -> %s mode=3 stratum=0 seq=%d xmt_lsb=0x%02x" % (t, attacker, server, seq, byte)); t+=1.0
with open("ntp_log.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T3_SOLVE = r'''
import re
server="198.51.100.42"
pat=re.compile(r'-> '+re.escape(server)+r'\b.*\bseq=(\d+)\b.*\bxmt_lsb=0x([0-9a-fA-F]{2})')
pairs=[]
for ln in open('ntp_log.txt').read().splitlines():
    m=pat.search(ln)
    if m:
        pairs.append((int(m.group(1)), int(m.group(2),16)))
pairs.sort()
message=bytes(b for _,b in pairs).decode('utf-8','replace')
'''

T3_GRADER = "python3 - <<'PY'\n" + PARSE + T3_SOLVE + r'''
s=get('SERVER'); m=get('MESSAGE')
ok = s is not None and s.lower()==server and m is not None and m.strip()==message
print('PASS' if ok else 'FAIL: server=%r message=%r (want %r / %r)' % (s,m,server,message))
PY'''

T3_REF = "python3 - <<'PY'\n" + T3_SOLVE + r'''
open('answer.txt','w').write("SERVER=%s\nMESSAGE=%s\n" % (server, message))
PY'''

TASKS.append(T(
 "net_ntp_exfil", "ntp-exfil-decode",
 T3_SETUP,
 "ntp_log.txt records NTP client packets, one per line, with fields including mode, stratum and 'xmt_lsb' "
 "(the least-significant byte of the transmit-timestamp fraction, shown as 0xNN). One internal host is tunnelling "
 "data out over NTP to a single destination server: those packets carry an extra 'seq=<n>' field and smuggle one "
 "payload byte per packet in xmt_lsb. Identify the exfil destination server IP, order its packets by seq, concatenate "
 "the smuggled bytes, and decode the ASCII message. Write answer.txt with exactly:\n"
 "SERVER=<destination ip>\nMESSAGE=<decoded ascii>",
 T3_GRADER, T3_REF))

# =====================================================================
# Task 4: BGP exact-prefix hijack, validated against RPKI ROAs
# =====================================================================
T4_SETUP = r'''python3 - <<'PY'
roas = [
    ("203.0.113.0/24", 24, 64500),
    ("198.51.100.0/24", 24, 64501),
    ("192.0.2.0/24", 24, 64502),
]
with open("roa.txt","w") as f:
    for net,ml,asn in roas:
        f.write("%s maxlen=%d asn=%d\n" % (net, ml, asn))
updates = [
    (1700300000, "203.0.113.0/24", "64500"),
    (1700300005, "198.51.100.0/24", "65001 64501"),
    (1700300010, "192.0.2.0/24", "65002 64502"),
    (1700300031, "203.0.113.0/24", "64666 65002"),   # HIJACK: origin 64666 not authorized
    (1700300040, "198.51.100.0/24", "65003 64501"),
]
with open("bgp_updates.txt","w") as f:
    for t,pfx,path in updates:
        origin=path.split()[-1]
        f.write('%d UPDATE prefix=%s as_path="%s" origin=%s\n' % (t, pfx, path, origin))
PY
'''

# RPKI check + find the single invalid update -- shared
BGP_SOLVE = r'''
import re, ipaddress
roas=[]
for ln in open('roa.txt').read().splitlines():
    m=re.match(r'(\S+)\s+maxlen=(\d+)\s+asn=(\d+)', ln)
    if m: roas.append((ipaddress.ip_network(m.group(1)), int(m.group(2)), int(m.group(3))))
def status(pfx, origin):
    P=ipaddress.ip_network(pfx)
    cov=[r for r in roas if P.version==r[0].version and P.subnet_of(r[0])]
    if not cov: return 'unknown'
    for net,ml,asn in cov:
        if P.prefixlen<=ml and asn==origin: return 'valid'
    return 'invalid'
invalids=[]
for ln in open('bgp_updates.txt').read().splitlines():
    m=re.search(r'prefix=(\S+)\s+as_path="([^"]*)"\s+origin=(\d+)', ln)
    if not m: continue
    pfx=m.group(1); origin=int(m.group(3))
    if status(pfx, origin)=='invalid':
        invalids.append((pfx, origin))
# exactly one invalid by construction
hijack_prefix, offending_as = invalids[0]
'''

T4_GRADER = "python3 - <<'PY'\n" + PARSE + BGP_SOLVE + r'''
p=get('PREFIX'); a=get('AS')
ok=False
if p is not None and a is not None:
    try:
        ok = p.strip()==hijack_prefix and int(re.sub(r'\D','',a))==offending_as
    except Exception: ok=False
print('PASS' if ok else 'FAIL: prefix=%r as=%r (want %r / %d)' % (p,a,hijack_prefix,offending_as))
PY'''

T4_REF = "python3 - <<'PY'\n" + BGP_SOLVE + r'''
open('answer.txt','w').write("PREFIX=%s\nAS=%d\n" % (hijack_prefix, offending_as))
PY'''

TASKS.append(T(
 "net_bgp_prefix_hijack", "bgp-hijack-rpki",
 T4_SETUP,
 "bgp_updates.txt lists BGP UPDATE messages: '<time> UPDATE prefix=<p> as_path=\"<a b c>\" origin=<asn>' "
 "(origin is the rightmost/originating AS). roa.txt holds the RPKI ROAs: '<prefix> maxlen=<n> asn=<authorized_asn>'. "
 "Exactly one update is an RPKI-invalid prefix hijack: a prefix covered by a ROA but originated by an AS the ROA does "
 "not authorize. Find it. Write answer.txt with exactly:\nPREFIX=<hijacked prefix>\nAS=<offending origin AS number>",
 T4_GRADER, T4_REF))

# =====================================================================
# Task 5: BGP more-specific (subprefix) hijack, RPKI max-length violation
# =====================================================================
T5_SETUP = r'''python3 - <<'PY'
roas = [
    ("192.0.2.0/23", 23, 64510),
    ("203.0.113.0/24", 24, 64520),
]
with open("roa.txt","w") as f:
    for net,ml,asn in roas:
        f.write("%s maxlen=%d asn=%d\n" % (net, ml, asn))
updates = [
    (1700400000, "192.0.2.0/23", "64510"),
    (1700400006, "203.0.113.0/24", "65020 64520"),
    (1700400021, "192.0.2.0/24", "64777 65099"),   # HIJACK: more-specific, maxlen+origin violation
    (1700400030, "198.51.100.0/24", "65030 64530"),  # unknown (no ROA) -> not invalid
]
with open("bgp_updates.txt","w") as f:
    for t,pfx,path in updates:
        origin=path.split()[-1]
        f.write('%d UPDATE prefix=%s as_path="%s" origin=%s\n' % (t, pfx, path, origin))
PY
'''

T5_GRADER = "python3 - <<'PY'\n" + PARSE + BGP_SOLVE + r'''
p=get('PREFIX'); a=get('AS')
ok=False
if p is not None and a is not None:
    try:
        ok = p.strip()==hijack_prefix and int(re.sub(r'\D','',a))==offending_as
    except Exception: ok=False
print('PASS' if ok else 'FAIL: prefix=%r as=%r (want %r / %d)' % (p,a,hijack_prefix,offending_as))
PY'''

T5_REF = "python3 - <<'PY'\n" + BGP_SOLVE + r'''
open('answer.txt','w').write("PREFIX=%s\nAS=%d\n" % (hijack_prefix, offending_as))
PY'''

TASKS.append(T(
 "net_bgp_subprefix_hijack", "bgp-hijack-rpki",
 T5_SETUP,
 "Same format as before: bgp_updates.txt has UPDATE lines and roa.txt has RPKI ROAs '<prefix> maxlen=<n> asn=<asn>'. "
 "One attacker announces a more-specific sub-prefix of a ROA-covered block, from an unauthorized origin and at a "
 "prefix length longer than the ROA's maxLength. That makes exactly one update RPKI-invalid. A prefix with no covering "
 "ROA at all is 'unknown', not a hijack. Identify the hijacked (sub)prefix and the offending origin AS. "
 "Write answer.txt with exactly:\nPREFIX=<hijacked prefix>\nAS=<offending origin AS number>",
 T5_GRADER, T5_REF))

# =====================================================================
# Task 6: STP root-bridge takeover (rogue bridge with lowest forged priority)
# =====================================================================
T6_SETUP = r'''python3 - <<'PY'
# legit root: prio 32768 mac aa:bb:cc:00:00:01 ; access switches prio 32768/49152
legit = [
    ("aa:bb:cc:00:00:01", 32768, "aa:bb:cc:00:00:01"),
    ("aa:bb:cc:00:00:02", 32768, "aa:bb:cc:00:00:01"),
    ("aa:bb:cc:00:00:03", 49152, "aa:bb:cc:00:00:01"),
]
rogue_mac="ce:ce:ce:de:ad:00"
rogue_prio=0
lines=[]
t=1700500000.0
# steady-state legit BPDUs
for r in range(4):
    for send,prio,root in legit:
        lines.append("%.3f BPDU sender_mac=%s root_priority=%d root_mac=%s port=1" % (t, send, prio, root))
        t+=2.0
    if r==2:
        # attacker starts claiming root with forged priority 0
        for _ in range(3):
            lines.append("%.3f BPDU sender_mac=%s root_priority=%d root_mac=%s port=1" % (t, rogue_mac, rogue_prio, rogue_mac))
            t+=2.0
with open("bpdu.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T6_SOLVE = r'''
import re
best=None  # (priority, sender_mac)
for ln in open('bpdu.txt').read().splitlines():
    m=re.search(r'sender_mac=(\S+)\s+root_priority=(\d+)\s+root_mac=(\S+)', ln)
    if not m: continue
    sender=m.group(1); prio=int(m.group(2))
    if best is None or prio<best[0]:
        best=(prio, sender)
rogue_priority, rogue_mac = best
'''

T6_GRADER = "python3 - <<'PY'\n" + PARSE + T6_SOLVE + r'''
mac=get('BRIDGE'); pr=get('PRIORITY')
ok=False
if mac is not None and pr is not None:
    try:
        ok = mac.strip().lower()==rogue_mac.lower() and int(re.sub(r'\D','',pr))==rogue_priority
    except Exception: ok=False
print('PASS' if ok else 'FAIL: bridge=%r priority=%r (want %r / %d)' % (mac,pr,rogue_mac,rogue_priority))
PY'''

T6_REF = "python3 - <<'PY'\n" + T6_SOLVE + r'''
open('answer.txt','w').write("BRIDGE=%s\nPRIORITY=%d\n" % (rogue_mac, rogue_priority))
PY'''

TASKS.append(T(
 "net_stp_root_takeover", "stp-root-takeover",
 T6_SETUP,
 "bpdu.txt is a capture of STP BPDU advertisements, one per line: "
 "'<time> BPDU sender_mac=<mac> root_priority=<n> root_mac=<mac> port=<n>'. The rightful root advertises a normal "
 "bridge priority. An attacker performs an STP root-takeover by injecting BPDUs advertising a forged, lower root "
 "priority so it wins the root election (lowest priority wins). Identify the rogue bridge's sender MAC and the forged "
 "priority it advertised. Write answer.txt with exactly:\nBRIDGE=<rogue sender mac>\nPRIORITY=<forged priority>",
 T6_GRADER, T6_REF))

# =====================================================================
# Task 7: ARP cache poisoning (gateway IP claimed by a second MAC, later)
# =====================================================================
T7_SETUP = r'''python3 - <<'PY'
# legit table
legit = {
    "192.168.1.1":"00:11:22:33:44:01",  # gateway
    "192.168.1.10":"00:11:22:33:44:0a",
    "192.168.1.11":"00:11:22:33:44:0b",
    "192.168.1.12":"00:11:22:33:44:0c",
    "192.168.1.53":"00:11:22:33:44:53",
}
attacker="de:ad:be:ef:00:99"
lines=[]
t=1700600000.0
# baseline: legit is-at replies (gateway announced early & correctly)
seq=[
    ("192.168.1.1","00:11:22:33:44:01"),
    ("192.168.1.10","00:11:22:33:44:0a"),
    ("192.168.1.11","00:11:22:33:44:0b"),
    ("192.168.1.1","00:11:22:33:44:01"),
    ("192.168.1.12","00:11:22:33:44:0c"),
    ("192.168.1.53","00:11:22:33:44:53"),
    ("192.168.1.10","00:11:22:33:44:0a"),
]
for ip,mac in seq:
    lines.append("%.3f ARP is-at sender_mac=%s sender_ip=%s" % (t, mac, ip))
    t+=3.0
# attack: attacker gratuitously claims the gateway IP (poisoning), several times, later
for _ in range(3):
    lines.append("%.3f ARP is-at sender_mac=%s sender_ip=%s" % (t, attacker, "192.168.1.1"))
    t+=3.0
with open("arp.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T7_SOLVE = r'''
import re
# collect is-at: ip -> list of (time, mac)
from collections import defaultdict
byip=defaultdict(list)
for ln in open('arp.txt').read().splitlines():
    m=re.search(r'^(\S+) ARP is-at sender_mac=(\S+)\s+sender_ip=(\S+)', ln)
    if not m: continue
    tm=float(m.group(1)); mac=m.group(2); ip=m.group(3)
    byip[ip].append((tm, mac))
# spoofed ip = the one with >1 distinct mac
spoofed=None
for ip,entries in byip.items():
    if len({mac for _,mac in entries})>1:
        spoofed=ip; break
# attacker mac = mac of the latest is-at for that ip
entries=sorted(byip[spoofed])
attacker_mac=entries[-1][1]
'''

T7_GRADER = "python3 - <<'PY'\n" + PARSE + T7_SOLVE + r'''
ip=get('IP'); mac=get('ATTACKER_MAC')
ok=False
if ip is not None and mac is not None:
    ok = ip.strip()==spoofed and mac.strip().lower()==attacker_mac.lower()
print('PASS' if ok else 'FAIL: ip=%r mac=%r (want %r / %r)' % (ip,mac,spoofed,attacker_mac))
PY'''

T7_REF = "python3 - <<'PY'\n" + T7_SOLVE + r'''
open('answer.txt','w').write("IP=%s\nATTACKER_MAC=%s\n" % (spoofed, attacker_mac))
PY'''

TASKS.append(T(
 "net_arp_poisoning", "arp-poisoning",
 T7_SETUP,
 "arp.txt is a list of ARP 'is-at' replies, one per line: "
 "'<time> ARP is-at sender_mac=<mac> sender_ip=<ip>'. The network runs normally until an attacker launches ARP cache "
 "poisoning: it sends gratuitous replies binding a victim IP (the default gateway) to the attacker's own MAC, "
 "overriding the legitimate mapping. Detect the poisoning: report the spoofed IP (the one now claimed by more than one "
 "MAC) and the attacker's MAC (the MAC that took it over, i.e. the most recent claimant). "
 "Write answer.txt with exactly:\nIP=<spoofed ip>\nATTACKER_MAC=<attacker mac>",
 T7_GRADER, T7_REF))

# =====================================================================
# Task 8: horizontal-vs-vertical port scan (one src, many dports on one dst)
# =====================================================================
T8_SETUP = r'''python3 - <<'PY'
scanner="10.0.0.66"
target="10.0.0.10"
lines=[]
t=1700700000.0
# benign traffic: a handful of hosts to common ports
benign=[
    ("10.0.0.20","10.0.0.10",443),
    ("10.0.0.21","10.0.0.10",80),
    ("10.0.0.22","10.0.0.11",22),
    ("10.0.0.20","10.0.0.12",443),
    ("10.0.0.23","10.0.0.10",53),
    ("10.0.0.24","10.0.0.11",443),
    ("10.0.0.25","10.0.0.10",80),
]
for s,d,p in benign:
    lines.append("%.3f %s:%d -> %s:%d TCP SYN" % (t, s, 40000+p%2000, d, p)); t+=0.5
# scan: scanner hits many distinct ports on target quickly
ports=[21,22,23,25,53,80,110,135,139,143,443,445,993,995,1433,1723,3306,3389,5900,8080,8443]
for p in ports:
    lines.append("%.3f %s:%d -> %s:%d TCP SYN" % (t, scanner, 50000+p%3000, target, p)); t+=0.02
import random
random.seed(7)
random.shuffle(lines)
with open("conn.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T8_SOLVE = r'''
import re
from collections import defaultdict
ports=defaultdict(set)
for ln in open('conn.txt').read().splitlines():
    m=re.search(r'\S+ (\S+):\d+ -> (\S+):(\d+)', ln)
    if not m: continue
    src=m.group(1); dst=m.group(2); dport=int(m.group(3))
    ports[(src,dst)].add(dport)
# scanner pair = max distinct dports on a single dst
(scanner,target)=max(ports, key=lambda k: len(ports[k]))
'''

T8_GRADER = "python3 - <<'PY'\n" + PARSE + T8_SOLVE + r'''
s=get('SCANNER'); d=get('TARGET')
ok = s is not None and d is not None and s.strip()==scanner and d.strip()==target
print('PASS' if ok else 'FAIL: scanner=%r target=%r (want %r / %r)' % (s,d,scanner,target))
PY'''

T8_REF = "python3 - <<'PY'\n" + T8_SOLVE + r'''
open('answer.txt','w').write("SCANNER=%s\nTARGET=%s\n" % (scanner, target))
PY'''

TASKS.append(T(
 "net_port_scan", "port-scan-detection",
 T8_SETUP,
 "conn.txt is a connection log, one TCP connection attempt per line: "
 "'<time> <src_ip>:<sport> -> <dst_ip>:<dport> TCP SYN' (lines are not time-ordered). Benign hosts each touch only a "
 "few services. One source is running a vertical port scan: it opens SYNs to many distinct destination ports on a "
 "single target host in a short span. Identify the scanner's source IP and the target IP it scanned. "
 "Write answer.txt with exactly:\nSCANNER=<scanner src ip>\nTARGET=<scanned dst ip>",
 T8_GRADER, T8_REF))

# =====================================================================
# Task 9: C2 beaconing (regular fixed interval among irregular benign flows)
# =====================================================================
T9_SETUP = r'''python3 - <<'PY'
c2="45.77.13.9"
victim="10.0.0.23"
lines=[]
# beacon: every ~60s, jitter alternates -2/+2 so mean is exactly 60
t=1700800000.0
diffs=[58,62]*20
lines.append("%.3f %s -> %s:443 bytes=512" % (t, victim, c2))
for i,d in enumerate(diffs[:19]):
    t+=d
    lines.append("%.3f %s -> %s:443 bytes=%d" % (t, victim, c2, 500+(i%7)))
# benign: irregular browsing to varied dsts/ports
import random
random.seed(11)
bt=1700800000.0
dsts=["142.250.72.14","151.101.1.69","13.107.42.14","104.16.132.229","93.184.216.34"]
for _ in range(60):
    bt+=random.randint(1,400)
    d=random.choice(dsts); p=random.choice([80,443,8443])
    src="10.0.0.%d" % random.choice([11,12,13,14,15,23])
    lines.append("%.3f %s -> %s:%d bytes=%d" % (bt, src, d, p, random.randint(200,5000)))
random.shuffle(lines)
with open("netflow.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T9_SOLVE = r'''
import re, statistics
from collections import defaultdict
groups=defaultdict(list)
for ln in open('netflow.txt').read().splitlines():
    m=re.search(r'^(\S+) (\S+) -> (\S+):(\d+) bytes=\d+', ln)
    if not m: continue
    tm=float(m.group(1)); src=m.group(2); dst=m.group(3); port=int(m.group(4))
    groups[(src,dst,port)].append(tm)
best=None  # (count, negstdev, key, interval)
for key, times in groups.items():
    if len(times)<10: continue
    times=sorted(times)
    diffs=[b-a for a,b in zip(times, times[1:])]
    if not diffs: continue
    sd=statistics.pstdev(diffs)
    mean=statistics.mean(diffs)
    # beacon = many events with low variability
    if sd < 10:
        cand=(len(times), key, round(mean))
        if best is None or cand[0]>best[0]:
            best=cand
count, (src,dst,port), interval = best
c2_ip=dst
'''

T9_GRADER = "python3 - <<'PY'\n" + PARSE + T9_SOLVE + r'''
ip=get('C2'); iv=get('INTERVAL')
ok=False
if ip is not None and iv is not None:
    try:
        ok = ip.strip()==c2_ip and abs(int(re.sub(r'\D','',iv))-interval)<=5
    except Exception: ok=False
print('PASS' if ok else 'FAIL: c2=%r interval=%r (want %r / %d+-5)' % (ip,iv,c2_ip,interval))
PY'''

T9_REF = "python3 - <<'PY'\n" + T9_SOLVE + r'''
open('answer.txt','w').write("C2=%s\nINTERVAL=%d\n" % (c2_ip, interval))
PY'''

TASKS.append(T(
 "net_c2_beaconing", "c2-beacon-detection",
 T9_SETUP,
 "netflow.txt is a flow log, one flow per line: '<epoch> <src_ip> -> <dst_ip>:<port> bytes=<n>' (unordered). Normal "
 "browsing flows hit varied destinations at irregular times. One infected host beacons to a C2 server at a near-constant "
 "interval (low jitter) over many callbacks. Identify the C2 destination IP and the approximate beacon interval in "
 "seconds. Write answer.txt with exactly:\nC2=<c2 ip>\nINTERVAL=<interval seconds>",
 T9_GRADER, T9_REF))

# =====================================================================
# Task 10: ICMP tunnel exfil (payload bytes reassembled by seq)
# =====================================================================
T10_SETUP = r'''python3 - <<'PY'
msg="DB_DUMP:users.sql;ROWS=10423"
data=msg.encode()
server="203.0.113.55"
attacker="10.0.0.77"
gw="10.0.0.1"
# 4 bytes per echo payload
chunks=[data[i:i+4] for i in range(0,len(data),4)]
lines=[]
t=1700900000.0
STD=bytes(range(0x61,0x69))  # 'abcdefgh' typical ping payload
ei=0
pattern=[0,1,1,0,1,1,1,0,1,1,1,0,1,1]
for p in pattern:
    if p==1 and ei<len(chunks):
        payload=chunks[ei].hex()
        lines.append("%.3f ICMP echo-request id=0x4d2 seq=%d src=%s dst=%s payload=%s" % (t, ei, attacker, server, payload)); ei+=1
    else:
        lines.append("%.3f ICMP echo-request id=0x1a4 seq=%d src=%s dst=%s payload=%s" % (t, ei, "10.0.0.40", gw, STD.hex()))
    t+=0.5
while ei<len(chunks):
    payload=chunks[ei].hex()
    lines.append("%.3f ICMP echo-request id=0x4d2 seq=%d src=%s dst=%s payload=%s" % (t, ei, attacker, server, payload)); ei+=1; t+=0.5
with open("icmp_log.txt","w") as f:
    f.write("\n".join(lines)+"\n")
PY
'''

T10_SOLVE = r'''
import re
server="203.0.113.55"
pat=re.compile(r'seq=(\d+)\s+src=\S+\s+dst='+re.escape(server)+r'\s+payload=([0-9a-fA-F]+)')
chunks=[]
for ln in open('icmp_log.txt').read().splitlines():
    m=pat.search(ln)
    if m:
        chunks.append((int(m.group(1)), m.group(2)))
chunks.sort()
raw=''.join(h for _,h in chunks)
message=bytes.fromhex(raw).decode('utf-8','replace')
'''

T10_GRADER = "python3 - <<'PY'\n" + PARSE + T10_SOLVE + r'''
s=get('SERVER'); m=get('MESSAGE')
ok = s is not None and s.strip()==server and m is not None and m.strip()==message
print('PASS' if ok else 'FAIL: server=%r message=%r (want %r / %r)' % (s,m,server,message))
PY'''

T10_REF = "python3 - <<'PY'\n" + T10_SOLVE + r'''
open('answer.txt','w').write("SERVER=%s\nMESSAGE=%s\n" % (server, message))
PY'''

TASKS.append(T(
 "net_icmp_tunnel_exfil", "icmp-exfil-decode",
 T10_SETUP,
 "icmp_log.txt records ICMP echo-request packets, one per line: "
 "'<time> ICMP echo-request id=<id> seq=<n> src=<ip> dst=<ip> payload=<hex>'. Ordinary pings to the LAN gateway carry a "
 "standard filler payload. One host runs an ICMP tunnel: it sends echo-requests to a single external host whose payloads "
 "are consecutive hex-encoded chunks of stolen data, ordered by seq. Identify that external destination and reassemble "
 "the payloads (in seq order) into the plaintext. Write answer.txt with exactly:\n"
 "SERVER=<external dst ip>\nMESSAGE=<decoded plaintext>",
 T10_GRADER, T10_REF))

# =====================================================================
# Self-test: setup -> grader must NOT pass ; setup -> reference -> grader MUST pass
# =====================================================================
def run(script, workdir):
    env=dict(os.environ); env['W']=workdir
    r=subprocess.run(['bash','-c',script], cwd=workdir, env=env,
                     capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr

def self_test(task):
    wd=tempfile.mkdtemp(prefix='nt_')
    try:
        rc,out,err=run(task['setup_sh'], wd)
        if rc!=0:
            return False, "setup rc=%d err=%s" % (rc, err.strip()[-400:])
        # grader before solving -> must NOT be PASS
        rc,out,err=run(task['grader_sh'], wd)
        pre=out.strip()
        if pre.split('\n')[-1:] == ['PASS'] or pre=='PASS':
            return False, "grader PASSed before solution: %r" % pre
        # reference solution
        rc,out,err=run(task['reference_solution_sh'], wd)
        if rc!=0:
            return False, "reference rc=%d err=%s" % (rc, err.strip()[-400:])
        # grader after solving -> MUST be PASS
        rc,out,err=run(task['grader_sh'], wd)
        post=out.strip()
        if post!='PASS':
            return False, "grader after reference != PASS: %r (err=%s)" % (post, err.strip()[-300:])
        return True, "ok"
    finally:
        shutil.rmtree(wd, ignore_errors=True)

def main():
    passed=[]
    for t in TASKS:
        ok, why = self_test(t)
        t['self_test_passed']=bool(ok)
        status = "PASS" if ok else "FAIL"
        print("[%s] %-28s %s" % (status, t['id'], "" if ok else why), file=sys.stderr)
        if ok:
            # strip helper key not needed in output schema but keep 'notes'
            passed.append(t)
    out_path="/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/tasks_network.json"
    with open(out_path,"w") as f:
        json.dump({"tasks":passed}, f, indent=1)
    print("VALIDATED %d / %d -> %s" % (len(passed), len(TASKS), out_path), file=sys.stderr)

if __name__=="__main__":
    main()
