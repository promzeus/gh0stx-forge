#!/usr/bin/env python3
# Build a hardware-grounded SIM800C AT-command SFT battery. Every example shows the CORRECT
# command and the REAL module response captured live from /dev/ttyUSB0 — so the model learns
# the actual command vocabulary instead of inventing plausible-looking names. Correction
# examples run the wrong command too, capturing the module's real ERROR, then the right one.
import json, subprocess, os, sys

BASE = "/home/gh0stx/octest/night"
AT_SFT = f"{BASE}/dataset/at_sft.jsonl"
OUT = f"{BASE}/dataset/at_battery_sft.jsonl"
SYS = json.loads(open(AT_SFT).readline())["messages"][0]["content"]  # reuse the training rules

def run_at(cmds):
    """Send AT commands to the module, return {cmd: [response lines]} using at_send.py markers."""
    inp = "".join(c + "\n" for c in (["ATE0"] + cmds))  # echo off for clean transcripts
    r = subprocess.run(["sudo", "python3", "/tmp/at_send.py"], input=inp,
                       capture_output=True, text=True, timeout=60)
    out = {}
    cur = None
    for line in r.stdout.splitlines():
        if line.startswith("<<< "):
            cur = line[4:].strip(); out[cur] = []
        elif cur is not None and line.strip():
            out[cur].append(line.strip())
    return out

def transcript(cmd, resp):
    return cmd + "\n" + "\n".join(resp)

# ---- read capabilities: task -> correct command(s) + phrasings + interpretation ----
READ = [
 dict(cmds=["AT+CGSN"], ask=[
    "Как прочитать IMEI модуля SIM800C?", "Выведи IMEI модема.",
    "Какой IMEI у модуля на /dev/ttyUSB0?", "Read the SIM800C IMEI.",
    "Покажи заводской серийник (IMEI) модуля.", "Верни 15-значный IMEI."],
    exp="`AT+CGSN` возвращает 15-значный IMEI — заводской идентификатор железа, от SIM не зависит."),
 dict(cmds=["AT+CIMI"], ask=[
    "Как узнать IMSI SIM-карты в SIM800C?", "Выведи IMSI карты.",
    "What AT command returns the IMSI on SIM800C?", "Прочитай IMSI из SIM.",
    "Дай IMSI абонента."],
    exp="`AT+CIMI` читает IMSI прямо из SIM. Первые 3 цифры — MCC (231 = Словакия), следующие 2 — MNC."),
 dict(cmds=["AT+CSQ"], ask=[
    "Как проверить уровень сигнала на SIM800C?", "Выведи качество сигнала.",
    "Signal strength on the module?", "Какой RSSI сейчас?", "Покажи CSQ.",
    "Насколько хорошо ловит модем?"],
    exp="`AT+CSQ` даёт `+CSQ: <rssi>,<ber>`. rssi 0..31: 2..30 линейно = -109..-53 dBm, 99 = нет сигнала. ber — качество, 0 лучшее."),
 dict(cmds=["AT+CPIN?"], ask=[
    "Как проверить, разблокирована ли SIM (PIN)?", "Нужен ли PIN карте?",
    "SIM PIN status on SIM800C?", "Статус CPIN.", "SIM готова к работе?"],
    exp="`AT+CPIN?` → `READY` значит SIM разблокирована и готова; `SIM PIN` требует ввода PIN командой `AT+CPIN=\"nnnn\"`."),
 dict(cmds=["AT+CREG?"], ask=[
    "Как узнать статус регистрации в сети?", "Модем зарегистрирован в сети?",
    "Network registration status?", "Проверь CREG.", "Почему нет сети — проверь регистрацию."],
    exp="`AT+CREG?` → `+CREG: <n>,<stat>`. stat: 0 не ищет, 1 домашняя сеть, 2 поиск, 3 отказ регистрации, 5 роуминг."),
 dict(cmds=["AT+COPS?"], ask=[
    "Какой оператор выбран на модеме?", "Выведи текущего оператора.",
    "Current operator on SIM800C?", "Проверь COPS."],
    exp="`AT+COPS?` показывает режим выбора и текущего оператора. Без регистрации имя оператора пустое."),
 dict(cmds=["AT+CGMM"], ask=[
    "Как узнать модель модуля?", "Выведи модель модема.", "Module model?", "Что за железо на порту?"],
    exp="`AT+CGMM` возвращает модель модуля."),
 dict(cmds=["AT+CGMR"], ask=[
    "Версия прошивки модуля?", "Выведи firmware модема.", "Firmware revision of the module?", "Проверь ревизию ПО."],
    exp="`AT+CGMR` возвращает версию прошивки (revision)."),
 dict(cmds=["AT+CGMI"], ask=[
    "Кто производитель модуля?", "Выведи вендора модема.", "Module manufacturer?"],
    exp="`AT+CGMI` возвращает производителя модуля (SIMCom)."),
 dict(cmds=["AT+CCLK?"], ask=[
    "Как прочитать часы модуля?", "Выведи время RTC модема.", "Module clock via AT?"],
    exp="`AT+CCLK?` возвращает время RTC в формате `\"yy/MM/dd,hh:mm:ss±zz\"`."),
 dict(cmds=["AT+CFUN?"], ask=[
    "Как проверить режим функциональности модуля?", "Полный ли функционал у модема?", "CFUN state?"],
    exp="`AT+CFUN?` → 1 полный функционал, 0 минимальный, 4 радио выключено (airplane)."),
 dict(cmds=["AT"], ask=[
    "Как проверить, отвечает ли модем?", "Пинг модуля по AT.", "Is the modem responsive?", "Модем живой?"],
    exp="Голая `AT` — проверка связи: `OK` значит модуль отвечает на UART."),
 dict(cmds=["AT+CCID"], ask=[
    "Как прочитать ICCID SIM-карты?", "Выведи серийник SIM (ICCID).",
    "SIM ICCID via AT?", "Номер SIM-карты (CCID)?"],
    exp="`AT+CCID` возвращает ICCID — заводской номер самой SIM-карты (не путать с IMSI)."),
 dict(cmds=["AT+GSN"], ask=[
    "Есть ли короткий вариант чтения IMEI кроме AT+CGSN?", "AT+GSN что вернёт?",
    "IMEI через AT+GSN?"],
    exp="`AT+GSN` — синоним `AT+CGSN`, тоже отдаёт IMEI. Оба валидны на SIM800C."),
 dict(cmds=["AT+CMGF=1"], ask=[
    "Как перевести модуль в текстовый режим SMS?", "Включи текстовый режим для SMS.",
    "Set SMS text mode on SIM800C.", "Перед отправкой SMS какой режим ставить?"],
    exp="`AT+CMGF=1` включает текстовый режим SMS (0 — PDU). После него отправка: `AT+CMGS=\"+number\"` → модуль ответит `>` , вводишь тело и завершаешь Ctrl-Z (0x1A). Доставка требует регистрации в сети (`AT+CREG?` = 1 или 5)."),
]

# ---- correction: plausible-wrong command -> real ERROR -> correct command ----
FIX = [
 dict(wrong="AT+CSIMEI", right="AT+CGSN", ask=[
    "Читаю IMEI через AT+CSIMEI — не работает, почему?",
    "AT+CSIMEI выдаёт ошибку, как правильно взять IMEI?",
    "Why does AT+CSIMEI fail for IMEI on SIM800C?"],
    exp="`AT+CSIMEI` нет в наборе SIM800C — модуль отвечает `ERROR`. IMEI отдаёт `AT+CGSN`."),
 dict(wrong="AT+RSS?", right="AT+CSQ", ask=[
    "AT+RSS? не показывает сигнал, что не так?",
    "Пробую AT+RSS? для уровня сигнала — ошибка.",
    "Signal via AT+RSS? returns ERROR — what is correct?"],
    exp="`AT+RSS?` не существует у SIM800C — `ERROR`. Уровень сигнала даёт `AT+CSQ`."),
 dict(wrong="AT+IMSI", right="AT+CIMI", ask=[
    "AT+IMSI не отдаёт IMSI, почему?",
    "Как правильно, AT+IMSI ругается ошибкой?"],
    exp="`AT+IMSI` — не команда SIM800C, вернёт `ERROR`. IMSI читается через `AT+CIMI`."),
 dict(wrong="AT+CGIMEI", right="AT+CGSN", ask=[
    "AT+CGIMEI выдаёт ERROR при чтении IMEI.",
    "Не могу взять IMEI через AT+CGIMEI."],
    exp="`AT+CGIMEI` выдуман — модуль отвечает `ERROR`. Верная команда IMEI — `AT+CGSN`."),
 dict(wrong="AT+CGIM", right="AT+CGSN", ask=[
    "AT+CGIM не читает IMEI, что вместо неё?",
    "Пробовал AT+CGIM — ошибка."],
    exp="`AT+CGIM` не существует у SIM800C — `ERROR`. IMEI даёт `AT+CGSN`."),
 dict(wrong="AT+CGQ", right="AT+CSQ", ask=[
    "AT+CGQ не показывает сигнал.",
    "Сигнал через AT+CGQ выдаёт ERROR."],
    exp="`AT+CGQ` выдумана — `ERROR`. Качество сигнала читает `AT+CSQ`."),
 dict(wrong="AT+CIMS", right="AT+CIMI", ask=[
    "AT+CIMS не отдаёт IMSI.",
    "Как правильно, AT+CIMS ругается?"],
    exp="`AT+CIMS` не команда SIM800C — `ERROR`. IMSI читается через `AT+CIMI`."),
 dict(wrong="AT+CMSI", right="AT+CIMI", ask=[
    "AT+CMSI выдаёт ошибку при чтении IMSI.",
    "Не работает AT+CMSI."],
    exp="`AT+CMSI` выдумана — `ERROR`. Верная для IMSI — `AT+CIMI`."),
 dict(wrong="AT+SIGNAL", right="AT+CSQ", ask=[
    "AT+SIGNAL не показывает уровень сигнала.",
    "Пробую AT+SIGNAL — ERROR."],
    exp="`AT+SIGNAL` не существует — `ERROR`. Уровень сигнала — `AT+CSQ`."),
]

def main():
    exs = []
    # read tasks: run each command set once, template every phrasing
    for t in READ:
        resp = run_at(t["cmds"])
        blocks = [transcript(c, resp.get(c, ["(no response)"])) for c in t["cmds"]]
        body = "\n".join(blocks)
        lead = f"Команда `{t['cmds'][0]}`" if len(t["cmds"]) == 1 else "Команды"
        assistant = f"{lead} — вот реальный ответ модуля:\n```\n{body}\n```\n{t['exp']}"
        for q in t["ask"]:
            exs.append(dict(messages=[{"role": "system", "content": SYS},
                                      {"role": "user", "content": q},
                                      {"role": "assistant", "content": assistant}],
                            meta={"domain": "sim800c_at", "cmd": t["cmds"][0], "grounded": True}))
    # correction tasks: run wrong then right, show both
    for t in FIX:
        rw = run_at([t["wrong"]]); rr = run_at([t["right"]])
        wb = transcript(t["wrong"], rw.get(t["wrong"], ["ERROR"]))
        rb = transcript(t["right"], rr.get(t["right"], ["(no response)"]))
        assistant = (f"Проверил на модуле. `{t['wrong']}` отбивается ошибкой:\n```\n{wb}\n```\n"
                     f"Верная команда отвечает:\n```\n{rb}\n```\n{t['exp']}")
        for q in t["ask"]:
            exs.append(dict(messages=[{"role": "system", "content": SYS},
                                      {"role": "user", "content": q},
                                      {"role": "assistant", "content": assistant}],
                            meta={"domain": "sim800c_at", "correction": t["wrong"], "grounded": True}))
    with open(OUT, "w") as f:
        for e in exs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote {len(exs)} examples -> {OUT}")
    print("read tasks:", len(READ), "| correction tasks:", len(FIX))

if __name__ == "__main__":
    main()
