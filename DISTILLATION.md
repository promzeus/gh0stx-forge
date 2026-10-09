# DISTILLATION SPEC — целевые сильные стороны модели

> Что строим и в чём модель обязана быть сильна. Проектные решения дизайн-сессий (2026-06-26),
> цифры сверены с фактом на 2026-07-02. Это ДИЗАЙН-НАМЕРЕНИЕ; фактически исполненный пайплайн —
> в PIPELINE.md, рецепты — в *-RECIPE.md / QUANT-DEPLOY.md.

## Цель

Компактная специализированная модель, помещающаяся на ОДНУ GB10, — полностью uncensored+послушный
offensive-security специалист для валидации СОБСТВЕННОЙ инфраструктуры владельца (авторизованный
red-team; гейтинг — operator-external control-plane checkpoint, НИКОГДА не «совесть» модели).
Критерий успеха — A/B победа над heretic122 по: корректности кода, частоте агентных зацикливаний
(режим отказа AEON-27B), успеху с первой попытки, скорости.

Факт: размер = **148.6B** (pruned_v2), GB10 = **122 ГБ** (не 130B / 128 ГБ из ранних черновиков).

## Матрица целевых возможностей (по приоритету)

1. **Offensive-security ядро** — web (XSS reflected/stored/DOM, SQLi, SSRF, IDOR/BOLA, CSRF, SSTI,
   auth/JWT bypass, path traversal, RCE, десериализация, mass-assignment); network (ARP/MITM/DNS,
   скан портов/сервисов, захват трафика); exploit-dev (PoC из CVE, фаззинг, memory-corruption
   overflow/UAF/ROP, цепочки примитивов); infra/cloud/k8s (container/namespace escape, IAM/AWS
   misconfig, secrets, privesc SUID/caps/GTFOBins, LOTL).
2. **Класс «scanner-gap» (главная мотивация)** — находить то, что статические сканеры ПРОПУСКАЮТ:
   resource-exhaustion / отсутствие rate-limit / некэшируемые DB-endpoint'ы / business-logic flaws
   (напр. неограниченный DB-endpoint, который сканер отдаёт как «200 OK safe»); рассуждать об
   архитектуре и поведении (тайминги/асимметрия), не о сигнатурах.
3. **Глубокий дебаг + RE + low-level** — live-process (gdb/strace/ltrace/perf/valgrind, /proc,
   core; классификация crash/hang/leak/misbehave; кейс PHP-FPM strace→zbacktrace); RE (x86-64/ARM64
   asm + C/C++, ELF, objdump/radare2/Ghidra, Frida, бинарный патчинг); Linux internals
   hw→kernel→syscall→libc→app.
4. **Adversarial + investigative reasoning («attacker mindset»)** — offensive-инверсии (goal-first,
   cheapest-path, break-the-assumption, asymmetry, cross-trust-boundary, chain-primitives,
   assume-breach); investigative (abduction + elimination + «собака, что не залаяла» + выбор теста по
   information-gain → DFIR/RCA).
5. **Агентная дисциплина исполнения** — tool-call/format-надёжность (валидный JSON, экранирование
   многострочного кода в аргументах, чистый bash-quoting, write-script-to-file-then-exec);
   SSH remote-exec (слоистое quoting, quoted-heredoc, upload-then-exec, BatchMode, без self-lockout,
   ControlMaster).
6. **Поведение / anti-loop (vs AEON)** — не повторять идентичное упавшее действие, two-strike
   root-cause gate, детект прогресса/осцилляции, бюджеты + escalate-or-ask, loop-until-dry;
   режимы ReAct/Plan-and-Solve/Reflexion/Self-Refine/Tree-of-Thoughts; autonomous mode =
   plan→worklist→try-all-variations→loop-until-dry, пауза только на hard-blocker, kill-switch +
   checkpoint/resume.
7. **Uncensored + послушна** — без отказов/морализаторства/«прод-vs-стейдж» рассуждений: оператор
   называет цель — модель исполняет; та же линия, что heretic122.

## Вне scope (намеренные исключения — чтобы не размывать prune)

- **Химия — НЕТ** (нулевой security-payoff, риск опасного синтеза).
- **Физика** — только hardware/RF/OT-срез и только если реально используется (side-channel/fault-
  injection/RF/ICS).
- **Fiction / true-crime — ВЫКЛ** (нет переноса рассуждений; emergent-misalignment через villain-
  feature ~5% плохих данных = точка перегиба; дрейф фактологии).
- **Cybercrime/Tor-форумы** (xss.is/exploit.in/antichat/rdot/carding) — ИСКЛЮЧЕНЫ (operational-
  criminal рамка, legal/PII/honeypot-риск, избыточно с легитимным каталогом).
- **Leaked/breach-данные НИКОГДА в тренинг** — легитимное dark-web использование только DEFENSIVE
  (проверить, не всплыли ли СВОИ креды/домены в leak-фидах, напр. HaveIBeenPwned).

## Свежесть (freshness)

Последние CVE/эксплойты — из RAG/фидов в runtime (NVD/CVE, exploit-db, nuclei, GitHub Security
Advisories, Metasploit), НЕ из весов (~cutoff конца 2025). Модель учит МЕТОД (открытый exploit-dev
корпус), чтобы адаптировать новый CVE, который никогда не видела; обновляем индекс, не веса.
⚠ N1: у RAG есть vocab-floor — retrieval не операционализирует тему, если её резидентные токены были
вырезаны prune'ом. Поэтому калибровочные добавки §OFFSEC-GAPS §3.10–11 — предпосылка для RAG.

Реализация (2026-09-10): `~/secrag/secrag` на gx10 — `secrag {cve|tag|search} <term>`, три
источника под `~/secrag/data/`: nuclei-templates (git, шаблоны по CVE/tag), exploitdb (git,
`files_exploits.csv`), metasploit-framework (git, `modules/**/*.rb`, ищет и по `CVE-YYYY-NNNNN`,
и по внутреннему формату `['CVE', 'YYYY-NNNNN']` в References) + kev.json/epss.csv для приоритета.
Источники — обычные git-клоны, живут только тем, что их пуллят руками; до 2026-09-10 nuclei-templates
и exploitdb стояли без обновления 3 месяца, metasploit не был склонирован вообще, хотя был назван
в списке источников с самого начала (см. абзац выше) — не подключался никогда. Обновлять: `git pull`
в каждой из трёх директорий.

## Бюджет памяти/скорости на GB10 (122 ГБ unified)

- Арх: 60 слоёв × 512 экспертов, top_k=10 активных, per-expert 12.58M params (~6.3 МБ NVFP4).
- Гибридное внимание: full_attention_interval=4 → только **15/60** full-attn слоёв; GQA 2 KV-heads,
  head_dim 256 → KV крошечный ~15.36 КБ/токен (vs 74 ГиБ у heretic122); linear-attn state ~0.1 ГБ.
- Контекст-цель 200k (нативный max 262144, без RoPE-scaling).
- **KV = BF16, не fp8** (fp8_e4m3 KV несовместим с DFlash на GB10/sm_121a, DFlash хотим): ~4.6 ГБ@150k,
  ~6.1 ГБ@200k, ~7.9 ГБ@256k.
- **Ключевой инсайт скорости:** декод задаётся top_k=10 активных экспертов + attention + DFlash,
  НЕ KEEP_FRAC. Prune освобождает RAM, но не ускоряет декод (те же 10 экспертов/токен). Значит
  «баланс интеллект↔скорость» = держать столько экспертов, сколько влезет, оставив RAM под DFlash-драфтер.
  KEEP_FRAC-рычаги (RAM весов): 0.32≈77 ГБ / 0.36≈85 ГБ / 0.40≈94 ГБ / 0.46≈107 ГБ (впритык).
- **Резерв сжатия (если вылезем за бюджет, по порядку):** (1) mixed-precision/3-бит на экспертах
  (512 пул, 10 активных → терпимо), ~15–20 ГБ; (2) ниже KEEP_FRAC (стоит интеллекта); (3) KV-quant
  KIVI/2-бит или eviction SnapKV/H2O/StreamingLLM (следить за DFlash-совместимостью); (4) context
  compression LLMLingua/RAG; (5) cold-expert NVMe offload (крайний). Стоимость темы в «слотах» —
  `pipeline/slot_accounting.py` (1 слот = 12.58M params ≈ 6.3 МБ NVFP4).

## Деплой-намерение

Агентный harness (инструменты nmap/nuclei/sqlmap/ZAP/debug-toolbox + SSH argv-array exec) +
RAG-CVE индекс + еженедельный K8s CronJob по целям владельца + defensive dark-web-exposure tool +
operator-external gate (auto_allow на own load-envs). Флаги/образ serve — QUANT-DEPLOY.md §4.

