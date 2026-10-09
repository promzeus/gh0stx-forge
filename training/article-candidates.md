# xakep.ru — кандидаты в обучение gh0stx

Отобрано из ~800 статей (категории hack/coding/admin/tricks, стр. 1-5). Все мапятся в
самопроверяемую задачу (setup фикстуры + grader). Полный текст Xakep пейволлит — брать через
telegra.ph-зеркала, как раньше. Ниже сгруппировано по направлениям; NEW = не покрыто текущим
набором (HTB, SQLi, deserialization, XOR/ECDSA, carving, web-enum, crackme, bash/net, Go, FF-wallet).

## 1. Исполнение в интерпретаторах / VM (NEW)
- Doom.ttf — код в VM шрифта TrueType — https://xakep.ru/2026/06/17/ttf-doom/ — распарсить TTF, прогнать байткод TrueType-VM, снять вывод

## 2. Деобфускация протекторов (NEW)
- PyArmor — https://xakep.ru/2026/04/02/pyarmor-reverse/ — DONE: `dx_pyarmor_blackbox` в
  `~/octest/night/authored_tasks.json` (2026-09-10). Реальный PyArmor 9.2.7, linux.aarch64
  фикстура; задача — не статическая декомпиляция байткода (нецелесообразна без версия-
  специфичного анпакера), а чёрный ящик: прогнать защищённый скрипт с разными входами,
  вывести общую формулу, сдать recovered.py, grade на held-out входах.
- Eazfuscator.NET (devirt) — https://xakep.ru/2026/03/12/eazfuscator-net/
- ConfuserEx — https://xakep.ru/2026/02/04/confuser-ex/
- Quick License Manager IL — https://xakep.ru/2025/12/03/qlm-deobfuscation/
- JSFuck JS — https://xakep.ru/2024/06/05/jsfuck/ — DONE ранее: `dx_jsfuck_string` уже был в
  tasks_deobf.json (не помечено здесь до 2026-09-10).
- LLVM-обфускация (CFF) — https://xakep.ru/2025/06/24/llvm-obfuscation/
- AutoIt3 decompile — https://xakep.ru/2025/07/04/autoit-reverse/
Задача: дать защищённый сэмпл, grade по восстановленным строкам/логике.

## 3. Внутренности malware-эвазии (NEW)
- API Hashing — https://xakep.ru/2023/10/06/api-hashing/ — резолв хешей импортов в WinAPI
- Hell's Gate — https://xakep.ru/2023/08/08/hells-gate/ — восстановить SSN / детектор техники
- Обход песочниц (VT/Kaspersky) — https://xakep.ru/2026/06/11/sandbox-bypass/ — детектор anti-sandbox
- Глушилка EDR через WFP — https://xakep.ru/2025/09/05/wfp-bypass/ — детект WFP-драйвера
- Тайный WinAPI — https://xakep.ru/2018/12/06/hidden-winapi/
- DLL sideload защита — https://xakep.ru/2023/12/26/dll-safety/

## 4. Криптоанализ лицензий/самопальных шифров (NEW)
- Delphi known-plaintext — https://xakep.ru/2026/06/18/delphi-reverse/
- Нелинейное key-уравнение — https://xakep.ru/2026/04/16/key-reversing/
- Самопальный GSM cipher — https://xakep.ru/2026/01/14/gsm-cypher-reverse/
- Rabbit-шифр — https://xakep.ru/2025/10/28/rabbit-reverse/ — вместо Rabbit сделан
  `dx_rc4_recover` (RC4, KSA+PRGA, known-key) — тот же класс навыка (implement-from-spec
  известного stream cipher), Rabbit сам не реализован (риск ошибки при ручной реализации
  из памяти без RFC-тествекторов под рукой пересилил точное соответствие пункту списка).
- ГОСТ Магма/Кузнечик — https://xakep.ru/2019/07/18/crypto-xor/
- Hashcracking бенчмарки — https://xakep.ru/2025/12/29/hashcracking-benchmarks/

## 5. Detection engineering (NEW) — кандидат пишет правило, grader реплеит фикстуру
- Sysmon+Sigma — https://xakep.ru/2024/02/16/sysmon-configs/
- Snort3 — https://xakep.ru/2026/03/26/snort3/
- Semgrep кастом — https://xakep.ru/2024/12/27/semgrep-custom/
- Semgrep IDOR — https://xakep.ru/2025/12/25/idor-hunt-2/
- eBPF Tetragon — https://xakep.ru/2025/04/09/ebpf-tetragon/
- Регексы для секретов — https://xakep.ru/2024/09/26/code-secrets/
- LLM-поиск секретов — https://xakep.ru/2024/12/04/llama-secrets/

## 6. Host/memory forensics (частично NEW)
- VolWeb/Volatility — https://xakep.ru/2026/02/05/volweb/
- Velociraptor VQL — https://xakep.ru/2026/03/20/velocirapor/
- WMI persistence — https://xakep.ru/2026/08/07/wmi-persistence/
- LNK-разбор — https://xakep.ru/2025/07/18/lnk-malware/
- Windows deleted-file traces (MFT/USN) — https://xakep.ru/2026/08/28/windows-forensic-recovery/
- NTFS MFT — https://xakep.ru/2020/01/29/ntfs-inside/
- ext3/ext4 recovery — https://xakep.ru/2020/10/21/extents-files-superblocks/
- USB forensic — https://xakep.ru/2022/12/09/usb-forensic-battle/
- Linux IR — https://xakep.ru/2023/05/03/linux-incident-response/

## 7. Parser-differential / logic-эксплойты (NEW)
- Go JSON/XML/YAML desync — https://xakep.ru/2025/06/20/go-parsing-bugs/
- OAuth 2.0/OIDC чек-лист — https://xakep.ru/2026/05/08/oauth-attacks-checklist/
- Race condition в бизнес-логике — https://xakep.ru/2025/08/27/race-conditon/
- Платёжная логика — https://xakep.ru/2025/10/10/payment-logic-hack/
- Слабый UUID IDOR — https://xakep.ru/2025/10/17/uuid-reverse/
- Prototype без загрязнения — https://xakep.ru/2026/08/14/prototype-no-pollution/

## 8a. Реальные disclosed CVE с воспроизводимым PoC (не xakep, добавлено 2026-09-10)
- NGINX Rift bundle (CVE-2026-42945/42946/40701/42934) — https://github.com/DepthFirstDisclosures/Nginx-Rift —
  heap overflow в ngx_http_rewrite_module (two-pass size/copy race, is_args рассинхронизация),
  версии 0.6.27–1.30.0 / Plus R32–R36. Есть `setup.sh` (собирает уязвимый nginx) + `poc.py` —
  воспроизводимо, но тяжело: heap feng shui через POST-тела, ASLR-зависимость, вероятна
  нестабильность PoC на другом окружении. У нас эта версия уже пропатчена (проверено, не наш
  риск сейчас) — держим на случай, если коллеги наткнутся на старый nginx где-то ещё.
- CVE-2026-42533 (map directive regex heap overflow, тот же репо, папка CVE-2026-42533/) —
  тоже с `setup.sh`+`poc.py`, full-chain с ASLR bypass через stream-leak. Аналогично тяжело.
- CVE-2026-42530 (QUIC/HTTP3 QPACK use-after-free, тот же репо) — только report.md, PoC-кода нет,
  не готово к таску без самостоятельной реализации эксплойта.
- **DFVULN-127, FFmpeg AV1 RTP depacketizer heap overflow** —
  https://github.com/DepthFirstDisclosures/ffmpeg-dfvuln127 (тот же автор, отдельный юзер-профиль,
  не organization). Heap overflow в `libavformat/rtpdec_av1.c` (TD-OBU двигает write-курсор без
  аллокации) → controlled instruction-pointer hijack (`0xdeadbeef`) через один RTP-пакет 183 байта.
  Полностью воспроизводимо: Dockerfile сам клонирует FFmpeg на пришпиленный уязвимый коммит
  (`d12791ef7f`) + `exploit.py` (RTSP/RTP сервер) + `run_demo.sh` с ожидаемым выводом —
  детерминированный краш, не зависит от heap feng shui как nginx-варианты выше. Лучший кандидат
  из всех disclosed-CVE в этом разделе для реальной задачи (setup.sh = их Dockerfile, grader =
  проверка PC==0xdeadbeef в GDB-выводе).

## 8. Web-CVE PoC-building (частично NEW)
- Copy Fail: 4 байта → root — https://xakep.ru/2026/08/20/copy-fail/
- ExifTool arg-injection RCE — https://xakep.ru/2025/08/14/exiftool-newline-hack/
- cron wildcard privesc — https://xakep.ru/2026/04/07/cron-wildcard/
- SSTI RAGFlow — https://xakep.ru/2026/08/11/ragflow-ssti/
- Langflow RCE — https://xakep.ru/2026/07/14/langflow-cve/
- Log4Shell overlooked — https://xakep.ru/2025/07/11/log4j-overlooked-exploits/
- W3 Total Cache RCE — https://xakep.ru/2026/02/10/w3-total-cache-cve/
- React2Shell (RSC) — https://xakep.ru/2025/12/24/react2shell-cve/
- WP Copypress webshell — https://xakep.ru/2025/10/07/wp-copypress-cve/

## 9. Firmware/hardware (NEW)
- TP-Link root без пароля — https://xakep.ru/2026/05/28/tp-link-reverse/
- IP-камера takeover — https://xakep.ru/2025/09/03/ip-camera-takeover/
- RCE в головном устройстве авто — https://xakep.ru/2025/10/30/car-modem-rce/
- Cisco firmware CVE — https://xakep.ru/2025/10/03/cisco-cve/
- D-Link firmware reverse — https://xakep.ru/2023/09/08/reversing-dlink/
- Спуф отпечатка — https://xakep.ru/2026/03/19/diy-fake-fingerprints/

## 10. AI/LLM security (NEW)
- Атаки на MCP — https://xakep.ru/2025/07/17/mcp-hacks/
- Тулзы пентеста LLM — https://xakep.ru/2026/04/28/ai-pentest-tools/
- LLM-стеганография — https://xakep.ru/2025/08/25/llm-steganography/
- YOLO против капчи — https://xakep.ru/2021/04/14/yolo/

## 11. Сетевые атаки/детект (частично NEW)
- DNS-туннель — https://xakep.ru/2018/09/07/dns-tunneling/
- NTP-эксфил — https://xakep.ru/2019/11/15/network-time-protocol/
- EIGRP poisoning — https://xakep.ru/2024/04/12/eigrp-scam/
- STP Yersinia — https://xakep.ru/2021/08/25/stp-yersinia/
- BGP route leak/hijack + RPKI — https://xakep.ru/2020/04/10/bgp-route-leaks/

## 12. Mobile (NEW)
- Android game hack (patch APK) — https://xakep.ru/2026/07/01/android-game-hacking/
- iOS anti-reverse — https://xakep.ru/2026/01/29/ios-anti-reverse/
- Android dangerous API — https://xakep.ru/2020/11/06/android-api-2/

## Уже покрыто (брать только для добора объёма)
HTB Cobblestone/Cypher/Conversor/Guardian/Gavel/Interpreter, web-«с самого начала»
(SSRF/XSS/SQLi/webshells), math-PHP-shell, browser stealer.

## 13. pwn.college ctf-archive (добавлено 2026-09-13)

Источник: `pwncollege/ctf-archive` (GitHub, 1310 CTF-задач, клонировано на gx10
`~/secrag/data/ctf-archive`) + `pwncollege/challenges` (текущая программа площадки —
проверено, редандант, только «Getting Started»-уровень, пропускать целиком). Схема
задачи: `DESCRIPTION.md` (условие) + `.init` (setup) + артефакт + `flagCheck`-бинарник
или прямое чтение `/flag` (грейдер). x86_64-бинарники требуют `docker run --platform
linux/amd64` + `qemu-user-static` — оба поставлены на gx10 13 сентября.

### DONE — 4 задачи, каждая проверена end-to-end и в живом пуле (authored_tasks.json)
- `cryptoversectf2023/microassembly` → `dx_microasm_recover` — кастомный ISA
  (DIV кладёт остаток в отдельный регистр, CMP его тестирует), флаг `cvctf{M1Cr@_c}`.
- `downunderctf2024/ternarybrained` → `dx_ternary_bf_decode` — bignum→base3→пары
  цифр→Brainfuck-опкоды→выполнить BF. Алгоритм сверен с официальным
  DownUnderCTF/Challenges_2024_Public writeup+solve.py, не только реверсом.
- `justctf2019/fsmir2` → `dx_fsm_verilog_walk` — флаг спрятан как путь в конечном
  автомате, сериализованном в Verilog `case`. Оригинальный `gen.py` требует живой
  `/flag` и удаляет себя после генерации — архив не содержит готового `.sv`,
  пересобрали сами (`random.seed(1337)`, синтетический флаг) той же логикой.
  Grader не хардкодит флаг — сам парсит `.sv` и прогоняет ответ через граф.
- `accessdeniedctf2022/llvm` → `dx_llvm_ir_invert` — LLVM IR (не asm): XOR23 →
  scatter → nibble-swap → scatter → rotate-left-1 → scatter → сравнение с `@flag`.
  Инвертировано за один проход, без подбора — IR полностью детерминирован.

### Крипто, кандидаты на следующий заход (не сделано, только шорт-лист от разведки)
- `zh3r0ctf2021/mersenne` (949 очков), `twist` (718), `numpymt` (930) — три вектора
  атаки на состояние Mersenne Twister.
- `sekaictf2025/unfairy` + `unfairyplusplus` — атака на UOV (постквантовая
  multivariate-подпись), тема почти без публичных разборов.
- `downunderctf2020/impeccable` — подделка ECC-подписи через заявленный биас в nonce.
- `corctf2021/lcg`, `babyrand`, `bank`, `friedrice` — кластер PRNG-атак одного CTF.
- `downunderctf2020/1337crypt` + `1337crypt v2` — сравнить эволюцию техники между
  турнирами.
- Пропуск: всё с base64/caesar/reverse-cipher в названии — textbook, уже покрыто.

### Реверс, подтверждён но не «декодер»-класс (ниже приоритет)
- `0x41414141ctf2021/hash` (keyjoinfile) — 32-бит ELF без section header,
  восстановить два ключа, формат `flag{key1+key2}`.
- `0x41414141ctf2021/ware` (skidw4re) — та же архитектура, ближе к крипто
  (восстановить plaintext после работы с крипто-библиотекой), не к VM/декодерам.
