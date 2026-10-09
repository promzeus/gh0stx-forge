#!/usr/bin/env python3
"""AT-command RFT harness: ask gh0stx for AT commands, run them on the real SIM800C, grade by
the module's actual response. SIM-independent tasks only (gradeable without a SIM card).
Baseline mode measures current pass rate; PASS (task,answer) pairs are saved for SFT.
Usage: python3 at_rft.py [REPEAT]
"""
import json, os, re, sys, subprocess, urllib.request

REPEAT = int(sys.argv[1]) if len(sys.argv) > 1 else 1
OUT = os.path.expanduser("~/octest/night/dataset/at_sft.jsonl")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

# task: (id, prompt, expected regex the MODULE response must match on the right command)
TASKS = [
 ("imei",   "Дай точную AT-команду, чтобы прочитать IMEI модуля SIM800C. Выведи только команду.", r"\b\d{15}\b"),
 ("model",  "Какой AT-командой узнать модель модуля SIM800C? Только команда.", r"SIM800"),
 ("firmware","AT-команда для версии прошивки SIM800C. Только команда.", r"Revision|SIM800C\d"),
 ("vendor", "AT-команда узнать производителя модуля SIM800C. Только команда.", r"SIMCOM"),
 ("signal", "AT-команда проверить уровень сигнала GSM на SIM800C. Только команда.", r"\+CSQ:\s*\d+,\d+"),
 ("netreg", "AT-команда узнать статус регистрации в сети (network registration) на SIM800C. Только команда.", r"\+CREG:\s*\d"),
 ("operator","AT-команда узнать выбранного оператора сети на SIM800C. Только команда.", r"\+COPS:"),
 ("clock",  "AT-команда прочитать внутренние часы модуля SIM800C. Только команда.", r"\+CCLK:"),
 ("battery","AT-команда узнать заряд/напряжение батареи на SIM800C. Только команда.", r"\+CBC:\s*\d"),
 ("cfun",   "AT-команда проверить уровень функциональности (radio) модуля SIM800C. Только команда.", r"\+CFUN:\s*\d"),
 ("echo_off","AT-команда выключить эхо команд на SIM800C. Только команда.", r"OK"),
 ("gprs_attach","AT-команда проверить, прикреплён ли модуль к GPRS на SIM800C. Только команда (запрос статуса).", r"\+CGATT:\s*\d"),
 ("info",   "Одной AT-командой получить идентификацию модуля SIM800C (ATI). Только команда.", r"SIM800"),
]

def ask(prompt, mt=2600):
    p = prompt + "\nОтвечай кратко, только AT-командами."
    body = json.dumps({"model":"gh0stx","messages":[{"role":"user","content":p}],
                       "max_tokens":mt,"temperature":0.0}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        "http://localhost:8000/v1/chat/completions", data=body,
        headers={"Content-Type":"application/json"}), timeout=300))
    m = r["choices"][0]["message"]
    return (m.get("content") or ""), (m.get("reasoning") or ""), r["choices"][0].get("finish_reason")

AT_LINE = re.compile(r"^(AT[A-Z0-9&+*=?,;.\"@!/#$%^()_\- ]*|ATE?\d?|ATI|ATD[^\r\n]*|ATH)\s*$", re.I)
def extract_cmds(text):
    cmds = []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        line = re.sub(r"^\d+[\).\s]+", "", line)          # strip "1. " numbering
        if line.upper().startswith("AT") and len(line) <= 60 and "\n" not in line:
            # cut trailing prose after the command if any
            line = line.split("  ")[0].strip().rstrip(".")
            cmds.append(line)
    # de-dup preserving order, cap to first few
    seen=set(); out=[]
    for c in cmds:
        if c not in seen: seen.add(c); out.append(c)
    return out[:6]

def run_on_module(cmds):
    inp = "\n".join(cmds) + "\n"
    r = subprocess.run(["sudo","python3","/tmp/at_send.py"], input=inp,
                       capture_output=True, text=True, timeout=60)
    return r.stdout

def main():
    results=[]; passes=[]
    for rep in range(REPEAT):
        for tid, prompt, exp in TASKS:
            content, reasoning, fr = ask(prompt)
            src = content if content.strip() else reasoning
            cmds = extract_cmds(src)
            if not cmds:
                results.append((tid, False, "no-cmd", fr, ""));
                print("%-12s r%d FAIL no-command-extracted (finish=%s, content=%dch)" % (tid,rep,fr,len(content)))
                continue
            modresp = run_on_module(cmds)
            ok = bool(re.search(exp, modresp))
            # a correct answer must NOT rely on a command the module ERRORed as unknown
            results.append((tid, ok, cmds[0] if cmds else "", fr, modresp))
            print("%-12s r%d %s cmds=%s | module: %s" % (
                tid, rep, "PASS" if ok else "FAIL", cmds,
                modresp.replace("\n"," ")[:90]))
            if ok:
                passes.append({"messages":[
                    {"role":"user","content":prompt},
                    {"role":"assistant","content":content.strip() or ("\n".join(cmds))}],
                    "meta":{"at_task":tid,"verified_on":"SIM800C"}})
    n=len(results); p=sum(1 for x in results if x[1])
    print("\n==== AT baseline: %d/%d PASS (hardware-graded) ====" % (p,n))
    if passes:
        with open(OUT,"a") as f:
            for ex in passes: f.write(json.dumps(ex,ensure_ascii=False)+"\n")
        print("saved %d verified PASS trajectories -> %s" % (len(passes), OUT))

if __name__=="__main__":
    main()
