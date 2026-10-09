# gb10/ — Qwen3.5-397B-A17B → ~141B «gh0stx» под одну GB10

Внутренний авторизованный R&D: компактная специализированная модель (offensive-security ассистент для
тестирования СОБСТВЕННОЙ инфраструктуры компании), которая помещается на **одну NVIDIA GB10 (122 ГБ)**.
Источник — `Qwen/Qwen3.5-397B-A17B`. Язык ответов — русский. AWS acct `123456789012`, test/dev, владелец
уполномочен по ИБ.

## ✅ Результат (РАБОТАЕТ, развёрнуто)

**gh0stx v4** — прунинг → абляция → NVFP4 → MTP, крутится на gx10 (GB10):

| Свойство | Значение |
|---|---|
| Параметры | ~141B (прунинг MoE 512→174 эксперта, math-fixed калибровка) |
| Цензура | снята (norm-preserving abliteration, refusal-rate 0%) |
| Квантизация | NVFP4 (compressed-tensors), ~84 ГБ → влезает в одну GB10 |
| Контекст | **128K** (нативно до 256K) |
| Ускорение | MTP spec-decode (`qwen3_5_mtp`, num_spec=3) → **~11 t/s, ×1.7** |
| Публикация | HF `promzeus/gh0stx-bf16` (141B bf16) + `promzeus/gh0stx-nvfp4` (84 ГБ, MTP вшит) |

**Артефакты:** S3 `s3://ai-monitoring-test-123456789012/{nvfp4_v4, decensored_v4 (bf16), pruned_v4}`.
Развёрнуто на gx10 (`~/v4_deploy.sh`). Живой лог всего пути — `~/distill/PROGRESS-CHECKPOINT.md`.

## Рецепт повтора (4 прохода — это и есть «что сработало»)

| # | Проход | Скрипт | Рецепт-док |
|---|--------|--------|-----------|
| 1 | PRUNE (MoE REAP, доменная калибровка) | `pipeline/pipeline_reap.py` + `calib_corpus.py` + `clean_offsec.py` | **PRUNE-RECIPE.md**, CALIB-COVERAGE-RESEARCH.md |
| 2 | ABLITERATE (norm-preserving) | `pipeline/merge_abliterate.py` | **ADJUST-RECIPE.md** |
| 3 | QUANT (NVFP4 W4A4) | `pipeline/nvfp4_quant.py` | **QUANT-DEPLOY.md** |
| 4 | MTP-graft + serve (vLLM/GB10) | `pipeline/graft_mtp.py` → `v4_deploy.sh` | **QUANT-DEPLOY.md**, `~/distill/GB10-DEPLOY-RUNBOOK.md` |

Ключевой урок (см. PROGRESS-CHECKPOINT): **калибровочный корпус решает, какие суб-навыки выживут при прунинге**
— нехватка math-примеров дала арифметическую деградацию (v3c), фикс в v4.

## Ускорение — что сработало / что нет

- ✅ **MTP** (deployed): num_spec-свип → **num_spec=3 = 11.1 t/s** оптимум. Детали: `~/distill/MTP-TUNING.md`.
- 🔄 **DFlash-драфтер** (продолжаем): инфра готова, но пока не бьёт MTP — `~/distill/DFLASH-TUNING.md`,
  чекпоинты `gx10:~/dflash_v4_drafter`, код `pipeline/dflash/`.
- ❌ **MTP self-distill** — регрессировал (exposure bias), свёрнут → `_archive/`.

## Доки

- **PIPELINE.md** — поэтапно каждый проход. **SCRIPTS.md** — по каждому скрипту.
- **DISTILLATION.md** — спека: цель, capability-matrix, scope.
- **PRUNE-RECIPE.md / ADJUST-RECIPE.md / QUANT-DEPLOY.md** — рецепты проходов.
- **OFFSEC-GAPS.md / OFFSEC-COVERAGE-MAP.md** — offsec gap-анализ + карта покрытия.
- **CALIB-COVERAGE-RESEARCH.md** — исследование калибровки (почему домены такие).
- **TEARDOWN.md** — снос облачных ресурсов (zero-cost).
- Оперативные (в `~/distill/`): PROGRESS-CHECKPOINT.md (журнал), GB10-DEPLOY-RUNBOOK.md, MTP-TUNING.md, DFLASH-TUNING.md.

## Жёсткие правила дизайна (держатся)

- Модель послушна; гейтинг — ВНЕ модели (operator control-plane, не «совесть» весов).
- IN scope: security / code / network / debug-RE / Linux + агентная дисциплина.
  OUT: химия, cybercrime-форумы; fiction A/B-gated ≤0.5%; breach-данные только defensive-RAG.
- Свежие CVE — через RAG/фиды в runtime, НЕ в весах (модель учит МЕТОД).

## Ops-правила

- Коммиты без строки «Co-Authored-By».
- Перед destructive-K8s: `kubectl config current-context == example-cluster`.
- Serve NVFP4: только Blackwell (GB10/B200/RTX50xx); bf16 KV (fp8 KV крашит с MTP); gpu-mem ≤0.87 на 128K.
- В открытом тексте — нейтральные формулировки (safety-фильтры ложно срабатывают на ИБ-термины).
