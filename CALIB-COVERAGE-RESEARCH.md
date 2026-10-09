# Покрытие калибровки при прунинге — исследование (для будущего re-prune)

> Статус: **ОТЛОЖЕНО** (решение 2026-07-02: сначала доводим v3 refusal-fix).
> Причина создания: гипотеза владельца — OFFSEC-корпус из 7 примеров недо-покрывает
> домен, часть экспертов навыка срезана при [1] prune. Подтверждено литературой.
> Расширение корпуса требует полного re-prune с этапа [1].

## 🚫 КАК СТРОИТЬ OFFSEC-КОРПУС (важный вывод 2026-07-03)
**Claude НЕ может массово генерировать offsec-корпус** — Opus safety-фильтр блокирует weaponized-категории
(Red Team/C2/malware/evasion — кат.7-12, и вероятно 1-6) даже в авторизованном контексте (проверено: агент
на кат.7-12 словил фильтр). ⇒ **offsec-корпус берём из ПУБЛИЧНЫХ HF-датасетов**, сэмплим/дедупим В POD'е
(calib_corpus.py HF_SLICES), НЕ генерим через Claude. Это и research рекомендовал (реальные данные > синтетика).
Датасеты: `AlicanKiraz0/Cybersecurity-Dataset-Fenrir-v2.0` (83920: OWASP/MITRE/крипто/cloud/DevSecOps/AI-sec),
`CyberNative/Code_Vulnerability_Security_DPO`, `WhiteRabbitNeo/WRN-Chapter-1`+`WRN-Chapter-2`,
`AlicanKiraz0/Cybersecurity-Dataset-v1`. Покрывают карту (12 категорий) по ширине. Тег domain=offsec.
Ручные cores (текущие 7 + карта) — для гарантии редких углов. Крипта: **75 Q&A уже сгенерены Claude**
(crypto/finance фильтр НЕ триггерит) → `gb10/pipeline/corpus/corpus_crypto.json` (fundamentals/смарт-контракты/
DeFi/кошельки/трейдинг/on-chain/tokenomics). code/math/precision/ru/general — как сейчас (HF + cores).

## ⚠️ ТРЕБОВАНИЯ К RE-PRUNE (добавлено 2026-07-03, из деплоя на GB10)

**1. Модель ДОЛЖНА быть меньше — под 122G GB10 с контекстом 150-200k.**
Текущая: 148.6B → **88G NVFP4** = СЛИШКОМ БОЛЬШАЯ. Доказано на деплое: vLLM резервирует
~96G под GPU/KV → на загрузку 88G файла остаётся 25.66G RAM → "Available RAM 25.66 GiB" <
checkpoint 87.78 GiB → mmap thrash **7.7 мин/шард**, а runai_streamer раздувает своп в потолок
16G (падение). На unified-памяти GPU-резерв и load-буферы — ОДИН пул 122G, не помещаются.
Пришлось добавить +32G swapfile (fstab) чтобы вообще грузилось.
- **TARGET: ~65-72G NVFP4** (модель + KV(200k) ~8-15G + load-буферы ~25G влезают в 122G с запасом).
- **KEEP_FRAC ~0.28-0.30** (сейчас 0.36) → ~110-120B (сейчас 148.6B). Ценой — меньше routed-экспертов.
- Компромисс: меньше экспертов = меньше специализации. Но остаёмся A17B-класс (top_k=10 активных).
  Совместить с расширением calib-корпуса (ниже): меньше оставляем, но правильные эксперты.

**2. Контекст 150-200k** (требование владельца, как daily-driver heretic122 с max-model-len 200000).
KV для 200k (fp8, ~15 full-attn слоёв гибрида) ≈ 6-15G — влезает если модель ~70G.

**3. Сохранить MTP-голову (для ускорения).** База Qwen3.5 имеет `mtp_num_hidden_layers: 1` (1 MTP-слой),
но наш пайплайн его ВЫРОНИЛ — в весах nvfp4_final_v2 ноль MTP-тензоров (config объявляет
mtp_num_hidden_layers/mtp_use_dedicated_embeddings, но весов нет). Причина: `AutoModelForCausalLM` при
загрузке базы не подхватывает MTP-модуль (он вне `model.model.layers`), и prune/adjust/quant его не несут.
MTP — единственный spec-decode, безопасный на гибридной GDN (ngram корраптит GDN, DFlash нужен драфтер).
Даёт ~40% single-stream. ЧТО НУЖНО: в pipeline_reap.py + merge_abliterate.py явно загружать/сохранять
MTP-слой; провести через NVFP4 (или ignore→BF16). Тогда серв с `--speculative-config
'{"method":"mtp","num_speculative_tokens":2}'` + `--kv-cache-dtype fp8_e5m2` (не e4m3 — spec-decode
edge-case). [[gh0stx-speed-config-gb10]]

**4. НОВЫЕ ДОМЕНЫ: физика / химия / электроника** (требование владельца 2026-07-03).
Сейчас в `calib_corpus.py` 6 доменов (russian/math/precision/offsec/code/general) — НЕТ физики, химии,
электроники → эти эксперты срезаны. Нужны для безобидных практических STEM-вопросов, например:
- **Физика:** фазовые переходы — холодная заморозка → вакуум → сублимация (лиофильная/сублимационная
  сушка); термодинамика, давление, вакуумные процессы.
- **Химия:** растворы и ПАВ — как смешать чтобы получить много пены/пузырей; полимеры/пластики —
  химобработка 3D-печати (ацетоновое сглаживание ABS для твёрдости/блеска; лёгкое растворение чтобы
  слои спеклись); растворители, реакции.
- **Электроника:** схемы, компоненты, пайка, питание (по аналогии).
ДОБАВИТЬ в CORES новые домены (physics/chemistry/electronics) с ручными Q&A (≥25 разных/под-навык, полные
вход+ответ) + HF-добор (напр. camel-ai/physics, camel-ai/chemistry).
⚠️ ГЛАВНОЕ (владелец 2026-07-03): **offsec ОСТАЁТСЯ приоритет №1 на весах** — новые домены НЕ за счёт
offsec, режем general. Предлагаемый баланс (сумма 1.0): **offsec 0.22 (№1)**, code 0.16, russian 0.12,
math 0.10, precision 0.10, general 0.08, physics 0.08, chemistry 0.08, electronics 0.06.
offsec+code=0.38 → кибербез доминирует, STEM-домены скромные.
⚠️ Натяжение: больше доменов + меньше экспертов (KEEP_FRAC 0.29) → каждый домен получит меньше экспертов
по union-floor. Балансировать; при просадке навыков — поднять KEEP_FRAC (но тогда модель больше).

**УТОЧНЁННАЯ СТРАТЕГИЯ (владелец 2026-07-03): НЕ распылять на много доменов, а УГЛУБИТЬ offsec.**
При маленьком keep-set (70G) много доменов = дилюция (каждый мало экспертов). Лучше: offsec приоритет
+ покрыть его С РАЗНЫХ СТОРОН (много под-навыков) чтобы зацепить ВСЕ security-эксперты → keep-set
тратится на комплексного security-специалиста. Сейчас offsec=7 примеров/7 углов → мало.
**ПОЛНАЯ КАРТА ПОКРЫТИЯ offsec (12 категорий, эталон от владельца 2026-07-03): `OFFSEC-COVERAGE-MAP.md`.**
12 категорий: 1.Web/API 2.Binary/RE/low-level 3.Crypto&Web3(DeFi) 4.Network/wireless 5.Windows/AD
6.Linux/containers 7.Cloud/DevOps 8.Mobile/IoT 9.Red-team/persistence/evasion 10.AI/ML adversarial
11.Social/physical 12.Recon/DFIR. КАЖДЫЙ под-навык карты → несколько РАЗНЫХ полных Q&A. Ширина>глубины.
Crypto/Web3 = категория 3 (внутри offsec, НЕ отдельный домен).
**РЕШЕНИЕ (владелец 2026-07-03): STEM-домены (physics/chemistry/electronics) ДРОПНУТЫ.** Не в тему при
фокусе «углубить offsec» — отъедали бы эксперты у security на малом бюджете 70G. Базовые STEM-знания
останутся через general+shared эксперты (база 397B general) — на редкий вопрос про сублимацию/пену/
3D-пластик ответит нормально, просто не STEM-специалист. Если STEM станет реально важен — это размен
(меньше security-фокус ИЛИ больше модель), пересмотреть тогда.

**+ ДОМЕН CRYPTO/БЛОКЧЕЙН (владелец 2026-07-03)** — В ТЕМУ (в отличие от STEM): синергичен с offsec
(смарт-контракт аудиты, DeFi-эксплойты, безопасность кошельков, on-chain форензика пересекаются с security).
Углы crypto: блокчейн-основы (консенсус PoW/PoS, Merkle, UTXO/account); смарт-контракты (Solidity/EVM/gas);
СК-безопасность (reentrancy/overflow/access-control/oracle-manipulation/flash-loan — пересечение с offsec);
DeFi (AMM/lending/staking/yield/flash-loans); кошельки/ключи (HD-wallet/seed/подписи/деривация); трейдинг/
биржи/tokenomics/on-chain-анализ/MEV/rug-pull/скамы. ≥25+ разных Q&A.

**ФИНАЛЬНЫЙ набор доменов (7, security+crypto ядро), offsec №1:**
offsec **0.26** (глубоко+широко), code 0.16, **crypto 0.14** (синергия с offsec), russian 0.12, math 0.11,
precision 0.11, general 0.10. Сумма 1.0. **offsec+crypto+code = 0.56** = ядро. БЕЗ physics/chemistry/electronics.

**Связка требований:** re-prune → модель которая (а) offsec ГЛУБОКО+ШИРОКО (все под-навыки, ≥25/угол,
№1, 0.26) + CRYPTO (0.14, синергия) + code/russian/math/precision/general, (б) ~70G NVFP4 для 200k на
122G, (в) СОХРАНЯЕТ MTP-голову (spec-decode ~40%). KEEP_FRAC~0.29. БЕЗ STEM.

## Проблема (замерено на нашем `calib_corpus.py`)

| Домен | Уникальных примеров | Доля | HF-добор |
|-------|--------------------:|-----:|----------|
| russian | 10 | 0.16 | gazeta |
| math | 12 | 0.13 | gsm8k |
| precision | 12 | 0.13 | нет (cores+повтор) |
| **offsec** | **7** | **0.24** | **нет (cores+повтор)** |
| code | 6 | 0.18 | the-stack-smol |
| general | 5 | 0.16 | c4 |

Доля offsec 0.24 от корпуса 800 → хотим ~192 примера, но уникальных 7 → остальные ~185 = **повтор**.

## Почему повтор бесполезен (подтверждено)

MoE top-k роутинг **детерминирован** — тот же вход даёт тот же набор экспертов (аналог greedy
decoding). Повтор идентичного текста НЕ активирует новых экспертов, только до-взвешивает уже
покрытых. Покрытие = число **различных под-тем**, не объём токенов. Практики дедуплицируют
(k-means → центроид кластера), а не повторяют.

Источник детерминизма: EASY-EP — «model consistently activates a sparse and stable subset of
experts on tasks within the same domain».

## Сколько РАЗЛИЧНЫХ примеров нужно

| Источник | Порог | Различных/домен |
|----------|-------|----------------:|
| EASY-EP (arXiv 2504.06792) | >90% критичных экспертов | 5 (пол) |
| **наши 7** | **~92%** | ← здесь мы |
| EASY-EP | ~99% стабильность keep-set | **25 (sweet spot)** |
| SparseGPT/Wanda/MoE-Pruner | стат. стандарт one-shot | **128** |

**Цель: ≥25 различных на под-навык, ~128 на домен.** Каждый — пара сотен токенов, разная под-тема.

## Наш failure mode назван в литературе

- **Mosaic Pruning (arXiv 2511.19822): «functional collapse»** — узкая калибровка выбрасывает
  специалистов, generalist-эксперты доминируют. +7-9% на спец-задачах при правильной калибровке.
- **REAP (arXiv 2510.13999):** калибровка только на C4 (без domain-данных) → **коллапс, 0%** на
  коде. Наш метод буквально назван по REAP (Router-weighted Expert Activation Pruning).
- REAP-числа: 1024 примера (≤110B) / 12228-24576 (≥110B), seq 2048-16384, C4 + domain-mix.

## HF-датасеты для разнообразного технического корпуса

- `bigcode/the-stack-dedup` — код 358 языков, дедуплицирован
- `bigcode/the-stack-v2-dedup` — 600+ языков, 3B+ файлов
- `AlicanKiraz0/Cybersecurity-Dataset-Fenrir-v2.0` — 83920 примеров (OWASP, MITRE ATT&CK, крипто,
  cloud, DevSecOps, AI security)
- `AlicanKiraz0/Cybersecurity-Dataset-v1` — 2500 пар (defensive)
- `Trendyol/Trendyol-Cybersecurity-Instruction-Tuning-Dataset`
- `CyberNative/Code_Vulnerability_Security_DPO` — пары secure/insecure код по языкам
- `WhiteRabbitNeo/WRN-Chapter-1`, `WRN-Chapter-2` — offensive+defensive корпус

## Процедура сборки (по EASY-EP), когда возьмёмся за re-prune

1. Перечислить под-навыки offsec (web-exploitation, binary/RE, крипто, cloud, network,
   secure-coding по языкам, AD-атаки, malware-техники, C2, фаззинг…).
2. Натянуть реальные примеры на КАЖДЫЙ под-навык из датасетов выше.
3. Дедуп + embedding/k-means кластеризация → центроид-ближайший на кластер.
4. ≥25 различных на под-навык (~128 на домен), пара сотен токенов каждый.
5. +10-20% общего текста (c4/OpenHermes) — не срезать generalist-экспертов (анти-collapse).
6. Расширить `CORES` в `calib_corpus.py`, включить HF-добор для offsec (сейчас cores-only).
7. Полный перезапуск [1] prune → [2] adjust → [3] quant → [4] deploy.

## Важное разграничение (не путать два рычага)

- `calib_corpus.py` (prune) — **что модель УМЕЕТ** (какие эксперты сохранить). ← это исследование
- `merge_abliterate.py` (adjust) — **что модель РАЗРЕШАЕТ** (убрать отказы). ← это v3-фикс

Abliteration-корпус мы УЖЕ расширили до 1579 пар (это про refusals, не про навыки). Покрытие
навыков — независимая ось, чинится только через re-prune.

## Источники

- EASY-EP / few-shot MoE pruning: https://arxiv.org/abs/2504.06792
- REAP: https://arxiv.org/abs/2510.13999 · https://github.com/CerebrasResearch/reap
- Mosaic Pruning (functional collapse): https://arxiv.org/abs/2511.19822
- MoE-Pruner: https://arxiv.org/abs/2410.12013
- Task-Specific Expert Pruning: https://arxiv.org/abs/2206.00277
