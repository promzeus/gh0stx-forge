#!/usr/bin/env python3
"""secrag — security RAG lookup for agents. Phase 1: keyword/CVE/tag over nuclei-templates + ExploitDB + Metasploit + KEV/EPSS."""
import sys, os, json, subprocess
BASE="/home/gh0stx/secrag/data"; NT=f"{BASE}/nuclei-templates"; EDB=f"{BASE}/exploitdb"; MSF=f"{BASE}/metasploit-framework/modules"

def grep_files(pat, maxn=8):
    try:
        out=subprocess.run(["grep","-rliE","--include=*.yaml",pat,NT],capture_output=True,text=True,timeout=40).stdout
        return [l for l in out.splitlines() if l][:maxn]
    except Exception: return []

def kev(cve):
    try:
        for v in json.load(open(f"{BASE}/kev.json")).get("vulnerabilities",[]):
            if v.get("cveID","").upper()==cve: return v
    except Exception: pass

def epss(cve):
    try:
        with open(f"{BASE}/epss.csv") as f:
            for ln in f:
                if ln.startswith(cve+","): p=ln.strip().split(","); return f"EPSS {p[1]} (percentile {p[2]})"
    except Exception: pass

def edb(term, maxn=10):
    try:
        with open(f"{EDB}/files_exploits.csv") as f:
            return [ln.strip() for ln in f if term.lower() in ln.lower()][:maxn]
    except Exception: return []

def msf(term, maxn=10):
    try:
        out=subprocess.run(["grep","-rliE","--include=*.rb",term,MSF],capture_output=True,text=True,timeout=40).stdout
        hits=[l for l in out.splitlines() if l]
    except Exception:
        hits=[]
    if not hits and term.upper().startswith("CVE-"):
        # module References blocks cite ['CVE', 'YYYY-NNNNN'] without the CVE- prefix
        alt = term.upper().replace("CVE-", "")
        try:
            out=subprocess.run(["grep","-rliE","--include=*.rb",alt,MSF],capture_output=True,text=True,timeout=40).stdout
            hits=[l for l in out.splitlines() if l]
        except Exception:
            pass
    return hits[:maxn]

def msf_name(path):
    try:
        for ln in open(path):
            if "'Name'" in ln and "=>" in ln:
                return ln.split("=>",1)[1].strip().strip(",").strip("'\" ")
    except Exception:
        pass
    return ""

def c_cve(cve):
    cve=cve.upper(); print(f"### {cve}")
    k=kev(cve); print("CISA KEV:", ("YES actively-exploited | "+k.get("vulnerabilityName","")) if k else "no")
    print(epss(cve) or "EPSS: n/a")
    tpl=grep_files(cve); print(f"\n## Nuclei templates ({len(tpl)}) — use as few-shot:")
    for t in tpl:
        print("\n--- "+os.path.relpath(t,NT)+" ---"); print(open(t).read())
    e=edb(cve); print(f"\n## ExploitDB ({len(e)}):")
    for h in e: print("  "+h)
    m=msf(cve); print(f"\n## Metasploit modules ({len(m)}):")
    for mm in m: print("  "+os.path.relpath(mm,MSF)+"  -- "+msf_name(mm))

def c_tag(tag):
    f=grep_files(f"tags:.*{tag}",12); print(f"## templates tag~{tag} ({len(f)}):")
    for t in f: print("  "+os.path.relpath(t,NT))

def c_search(term):
    f=grep_files(term,12); print(f"## nuclei ~{term} ({len(f)}):")
    for t in f: print("  "+os.path.relpath(t,NT))
    e=edb(term); print(f"## exploitdb ~{term} ({len(e)}):")
    for h in e: print("  "+h)
    m=msf(term); print(f"## metasploit ~{term} ({len(m)}):")
    for mm in m: print("  "+os.path.relpath(mm,MSF)+"  -- "+msf_name(mm))

if __name__=="__main__":
    if len(sys.argv)<3: print("usage: secrag {cve|tag|search} <term>"); sys.exit(1)
    {"cve":c_cve,"tag":c_tag,"search":c_search}.get(sys.argv[1],lambda x:print("unknown"))(sys.argv[2])
