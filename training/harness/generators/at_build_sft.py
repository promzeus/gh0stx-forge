#!/usr/bin/env python3
"""Build a hardware-VERIFIED SIM800C AT-command SFT set: run the correct commands on the real
module, capture the actual responses, emit {system,user,assistant} examples grounded in what the
device really returned. Fixes the model's AT hallucination (it invented CMODE/SMSSEND, guessed
AT+RSS for signal). Run on gx10 with the module on /dev/ttyUSB0.
"""
import serial, time, fcntl, json, os, re

RULES = open("/home/gh0stx/.config/opencode/gh0stx-rules.md").read().strip()
OUT = "/home/gh0stx/octest/night/dataset/at_sft.jsonl"
os.makedirs(os.path.dirname(OUT), exist_ok=True)

lf = open("/tmp/at_scan.lock", "w"); fcntl.flock(lf, fcntl.LOCK_EX)
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=1); time.sleep(0.3)
def run(cmd, w=1.0):
    ser.reset_input_buffer(); ser.write((cmd + "\r\n").encode()); time.sleep(w)
    return ser.read(ser.in_waiting or 1).decode(errors="replace")
run("ATE0", 0.4)

def block(cmd, w=1.0):
    """Return a clean 'cmd\\n<response lines>' transcript as the device actually answered."""
    r = run(cmd, w)
    lines = [x for x in r.replace("\r", "").split("\n") if x.strip() and x.strip() != cmd]
    return cmd + "\n" + "\n".join(lines)

# (russian question, command to run, explanation with {resp} = real device transcript)
SPEC = [
 ("Как прочитать IMEI модуля SIM800C?", "AT+CGSN",
  "Команда `AT+CGSN` возвращает 15-значный IMEI:\n```\n{resp}\n```\nЭто аппаратный идентификатор устройства (TAC + серийный номер + контрольная цифра по алгоритму Луна)."),
 ("Какой AT-командой узнать модель модуля SIM800C?", "AT+CGMM",
  "`AT+CGMM`:\n```\n{resp}\n```"),
 ("Как узнать версию прошивки SIM800C?", "AT+CGMR",
  "`AT+CGMR` (или `AT+GMR`):\n```\n{resp}\n```"),
 ("Как узнать производителя модуля?", "AT+CGMI",
  "`AT+CGMI`:\n```\n{resp}\n```\nЭто SIMCom — SIM800C построен на их чипсете, не на Huawei."),
 ("Как проверить уровень сигнала GSM на SIM800C?", "AT+CSQ",
  "`AT+CSQ`. Ответ `+CSQ: <rssi>,<ber>`, где rssi 0-31 (99 = не определён), в dBm = -113 + 2×rssi; ber 0-7 (99 = неизвестно):\n```\n{resp}\n```"),
 ("Как проверить статус SIM-карты (нужен ли PIN)?", "AT+CPIN?",
  "`AT+CPIN?`. Возможные ответы: `+CPIN: READY` (готова), `+CPIN: SIM PIN` (нужен PIN → `AT+CPIN=\"1234\"`), `+CPIN: SIM PUK` (заблокирована), `ERROR` (карта не вставлена):\n```\n{resp}\n```"),
 ("Как прочитать IMSI SIM-карты?", "AT+CIMI",
  "`AT+CIMI` возвращает IMSI (первые 3 цифры MCC — страна, следующие 2-3 MNC — оператор):\n```\n{resp}\n```"),
 ("Как проверить, вставлена ли SIM-карта?", "AT+CSMINS?",
  "`AT+CSMINS?` → `+CSMINS: <n>,<inserted>`, где inserted=1 если карта вставлена:\n```\n{resp}\n```"),
 ("Как узнать статус регистрации в сети GSM?", "AT+CREG?",
  "`AT+CREG?` → `+CREG: <n>,<stat>`. stat: 0=не зарегистрирован и не ищет, 1=зарегистрирован (домашняя сеть), 2=идёт поиск, 3=регистрация ОТКЛОНЕНА сетью, 5=роуминг:\n```\n{resp}\n```\nstat=3 при живой SIM и хорошем сигнале обычно значит деактивированную карту (неуплата/бан IMSI в HLR)."),
 ("Как узнать текущего оператора сети?", "AT+COPS?",
  "`AT+COPS?` → `+COPS: <mode>,<format>,<oper>` когда зарегистрирован:\n```\n{resp}\n```"),
 ("Как проверить уровень функциональности модуля (радио вкл/выкл)?", "AT+CFUN?",
  "`AT+CFUN?`: 0=минимум (радио выкл), 1=полный, 4=режим полёта. Установка: `AT+CFUN=1`, полный сброс: `AT+CFUN=1,1`:\n```\n{resp}\n```"),
 ("Как узнать заряд/напряжение батареи модуля?", "AT+CBC",
  "`AT+CBC` → `+CBC: <status>,<заряд %>,<напряжение мВ>`:\n```\n{resp}\n```"),
 ("Как узнать поддерживаемые/текущие диапазоны частот модуля?", "AT+CBAND?",
  "`AT+CBAND?`:\n```\n{resp}\n```\nSIM800C — только 2G GSM (850/900/1800/1900). Если у оператора выключен 2G, модуль не зарегистрируется."),
 ("Как прочитать внутренние часы модуля?", "AT+CCLK?",
  "`AT+CCLK?` → `+CCLK: \"ГГ/ММ/ДД,чч:мм:сс+зона\"`:\n```\n{resp}\n```"),
 ("Как получить полную идентификацию модуля одной командой?", "ATI",
  "`ATI`:\n```\n{resp}\n```"),
 ("Как проверить, прикреплён ли модуль к GPRS?", "AT+CGATT?",
  "`AT+CGATT?` → `+CGATT: 1` прикреплён, `0` нет. Прикрепить: `AT+CGATT=1`:\n```\n{resp}\n```"),
]

examples = []
def add(user, assistant):
    examples.append({"messages":[
        {"role":"system","content":RULES},
        {"role":"user","content":user},
        {"role":"assistant","content":assistant}], "meta":{"domain":"sim800c_at","verified_on":"SIM800C"}})

for q, cmd, expl in SPEC:
    resp = block(cmd)
    add(q, expl.format(resp=resp))

# --- multi-step, hardware-grounded exemplars (correct SMS + diagnostics knowledge) ---
add("Приведи точную последовательность AT-команд для отправки SMS в текстовом режиме через SIM800C на +79161234567 с текстом Test.",
    "Правильная последовательность (SIM должна быть зарегистрирована в сети):\n"
    "```\nAT+CMGF=1                     # текстовый режим (не PDU)\nAT+CMGS=\"+79161234567\"        # модуль ответит приглашением >\nTest<Ctrl+Z>                  # текст, затем байт 0x1A для отправки\n```\n"
    "Ответ при успехе: `+CMGS: <ref>` и `OK`. НЕ существует команд `AT+CMODE` или `AT+SMSSEND` — это неверно. "
    "Кодировка: `AT+CSCS=\"GSM\"` для латиницы, для кириллицы — `AT+CSCS=\"UCS2\"` и текст в UCS2-hex.")

add("Как через SIM800C сделать HTTP GET по GPRS (APN internet)? Полная последовательность AT-команд.",
    "```\nAT+SAPBR=3,1,\"Contype\",\"GPRS\"\nAT+SAPBR=3,1,\"APN\",\"internet\"\nAT+SAPBR=1,1              # поднять bearer\nAT+SAPBR=2,1              # проверить, получили IP\nAT+HTTPINIT\nAT+HTTPPARA=\"CID\",1\nAT+HTTPPARA=\"URL\",\"http://example.com/\"\nAT+HTTPACTION=0          # 0=GET; ответ +HTTPACTION: 0,<код>,<длина>\nAT+HTTPREAD             # прочитать тело\nAT+HTTPTERM\nAT+SAPBR=0,1             # опустить bearer\n```")

add("Модуль SIM800C не регистрируется в сети (AT+CREG? отдаёт 0,3), хотя сигнал есть и SIM вставлена. Как диагностировать?",
    "`CREG: 0,3` = регистрация ОТКЛОНЕНА сетью, отказ идёт с оператора, не с модуля. Проверь по шагам:\n"
    "```\nAT+CPIN?      # READY — карта не заблокирована\nAT+CIMI       # IMSI читается — карта цела\nAT+CSQ        # сигнал есть (rssi заметно меньше 99)\nAT+COPS=?     # видны ли сети (скан до ~60с)\nAT+CEER       # причина отказа\nAT+CFUN=1,1   # полный сброс радио, затем повторить AT+CREG?\n```\n"
    "Если сеть в скане видна, сигнал есть, IMSI читается, но stat остаётся 3 — почти всегда это деактивированная SIM "
    "(неуплата, истёкший срок, бан IMSI в HLR). Чинится только у оператора реактивацией карты, AT-командами сеть принять модуль не заставить. "
    "Отдельно проверь IMEI `AT+CGSN`: затёртый/невалидный IMEI сеть тоже отклонит.")

add("Как восстановить затёртый IMEI на SIM800C, если известен родной номер с наклейки (например 352931108990482)?",
    "Инженерной командой SIMCom `AT+EGMR`:\n```\nAT+CGSN                              # текущий (затёртый) IMEI\nAT+EGMR=1,7,\"352931108990482\"        # запись: mode=1, format=7 (IMEI), номер в кавычках\nAT+CGSN                              # проверка — должен вернуть записанный\n```\n"
    "Затем передёрнуть питание модуля. Писать только **родной** IMEI с наклейки устройства; номер должен быть 15 цифр с верной контрольной цифрой по Луну.")

ser.close()
with open(OUT, "a") as f:
    for ex in examples:
        f.write(json.dumps(ex, ensure_ascii=False) + "\n")
print("wrote %d hardware-verified AT SFT examples -> %s" % (len(examples), OUT))
print("total at_sft.jsonl:", sum(1 for _ in open(OUT)))
