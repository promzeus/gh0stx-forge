# SCRIPTS — по каждому скрипту: что делает и как запускать

> Все рабочие скрипты лежат в `pipeline/` (копии из рабочего каталога `~/distill/`, зеркало на
> `s3://ai-monitoring-test-123456789012/heal/`). Манифесты подов — в `pods/`.
> Порядок исполнения — см. PIPELINE.md. Обновлено: 2026-07-02.

## Легенда статусов

| Статус | Значение |
|--------|----------|
| ✅ FINAL | рабочий, часть валидированной цепочки — держим в toolkit |
| ⛔ SUPERSEDED | старая версия, заменена другим скриптом — удалена из toolkit |
| 🗄 HISTORICAL | исследованная ветка (heal), не в финальной цепочке — держим ради техники |

Общая для CPU-скриптов деталь: все патчат `transformers.modeling_utils.caching_allocator_warmup`
в no-op — иначе один `torch.empty()` на всю модель (~120 ГБ+) убивает узел по OOM ещё до загрузки.
Стек пиновый: `transformers==5.12.1` (использует `dtype=`, не `torch_dtype=`), `trust_remote_code=True`.

---

## ЭТАП [1] PRUNE

### `calib_corpus.py` ✅ FINAL
**Что делает.** Собирает сбалансированный, размеченный по доменам калибровочный корпус для Phase A
prune. Ручные «core»-примеры (полный вход+ответ) по 6 доменам (russian, math, precision, offsec,
code, general) + опциональный добор из стриминговых HF-датасетов до целевых долей. `assemble()`
возвращает список `{"text","domain"}`; тег домена нужен, чтобы считать выбор экспертов ПО ДОМЕНАМ.
**Доли (CORES):** russian 0.16 / math 0.13 / precision 0.13 / offsec 0.24 / code 0.18 / general 0.16.
**HF-источники:** russian=gazeta, math=gsm8k, code=the-stack-smol (гейтед → откат на cores), general=c4.
**Как запускать.** Библиотека, импортируется из `pipeline_reap.py`: `from calib_corpus import assemble`.
Standalone `python calib_corpus.py` — только smoke-тест (печатает гистограмму доменов, без prune).
**Грабли.** Без тега домена / при глобальной склейке текста воспроизводится ИСХОДНЫЙ баг (EN-global),
убивавший RU/math. HF-загрузка best-effort: при сбое домен молча падает на cores-only — проверяй
печатаемую гистограмму, иначе рискуешь prune на несбалансированном корпусе. Precision/offsec всегда
cores-only (нет чистого HF), их квота добирается повтором.

### `pipeline_reap.py` ✅ FINAL
**Что делает.** CPU-only прунер экспертов MoE — исполнитель, который произвёл `pruned_v2`
(397B → 148.6B; 512→184 экспертов/блок, 60 блоков, top_k=10, shared_expert НЕ трогается).
**Phase A:** forward-hook на `gate` каждого блока → top-k → bincount в счётчики ПО ДОМЕНАМ.
**Phase B:** keep-set = честный round-robin UNION топ-экспертов по доменам (анти-starvation floor),
добор глобальным топом; режет `gate_up_proj`/`down_proj`/`gate.weight`, патчит `num_experts`/`top_k`,
4 sanity-генерации, сохраняет BF16 шардами по 20 ГБ. **Выходы:** `pruned_v2`, `importance_v2.json`
(резюм), `prune_result_v2.json`.
**Как запускать** (рядом должен быть `calib_corpus.py`):
```bash
KEEP_FRAC=0.36 OUT_DIR=/pv/run397b_v2 MODEL_ID=/pv/397b DEVICE_MAP=cpu \
  CALIB_TARGET=256 MAXLEN=1024 USE_HF=1 python3 pipeline_reap.py
```
Резюмируемый: есть `importance_v2.json` → пропускает Phase A; есть `pruned_v2/config.json` → выходит.
**Параметры.** `KEEP_FRAC=0.36` (при просадке навыков — до 0.45); `FLOOR_FRAC` — читается, но НЕ
используется (floor даёт сам round-robin). `MAXLEN=2048`, `CALIB_TARGET=800`, `USE_HF=1`.
**Грабли.** ⚠ БАГ строка 129: лог ссылается на неопределённый `floor_n` → `NameError` в КОНЦЕ первого
Phase A (уже ПОСЛЕ записи `importance_v2.json`); при рестарте идёт resume-ветка и prune доходит до
конца. Починить перед «продакшеном»: убрать `floor_n` из лога. Нужно ~1.5 ТБ RAM (BF16 751 ГБ, пик
~760 ГБ). Жёсткий ассерт покрытия: домен с 0 обращений → abort («domain X got ZERO routing»).

### `pipeline_397b.py` ⛔ SUPERSEDED (удалён из toolkit)
Первая, СЛОМАННАЯ попытка prune: ОДИН глобальный счётчик экспертов (без тегов домена) на EN-only
offsec/code-корпусе → голодание RU/math/precision-экспертов → деградация. Заменён связкой
`calib_corpus.py` + `pipeline_reap.py`. Не использовать.

---

## ЭТАП [2] ADJUST

### `merge_abliterate.py` ✅ FINAL
**Что делает.** Снимает «вектор отказа» (Arditi 2406.11717), делая модель послушной. Грузит
`pruned_v2` полностью CPU-resident (`device_map=None` — нужны реальные тензоры для in-place правок),
опц. мержит q/k/v/o LoRA, меряет направление отказа (float64-Welford last-token hidden state,
~32 harmful vs ~32 harmless; d на слое `int(0.6×depth)=36`), затем ортогонализует d из всех писателей
остатка. Метод — **norm-preserving biprojected** (`ABL_NORM_PRESERVE=1`): восстанавливает L2-норму
строк → нет outlier'ов → безопасно для NVFP4. Вход `pruned_v2` → выход `decensored_v2` (BF16).
**Как запускать** (воспроизведение `decensored_v2`):
```bash
ABL_NORM_PRESERVE=1 ABL_DO_OPROJ=1 ABL_DO_GDN_OUT=1 ABL_DO_EXPERT_DOWN=1 \
ABL_DO_SHARED_DOWN=1 ABL_DO_EMBED=1 \
  python3 merge_abliterate.py --skip-lora \
    --model /pv/run397b_v2/pruned_v2 --out /pv/run397b_v2/decensored_v2
```
**Параметры.** `ABL_NORM_PRESERVE=1` — держать 1 (0 = плоская abliteration → коллапс под NVFP4,
только для A/B). `--skip-lora` ОБЯЗАТЕЛЕН для воспроизведения (иначе мержит heal-LoRA из `/data/ckpt150`
— это historical-путь). `ABLITERATE_LAYER=36` по умолчанию. `ABL_DO_*` (все ON) = полный набор
писателей: 15 o_proj + 45 linear_attn.out_proj + 60×184 experts.down_proj + 60 shared.down_proj + embed.
**Грабли.** ⚠ Докстринг и inline-комментарии УСТАРЕЛИ: называют in-house abliteration «crude path that
damaged the model» и советуют путь через Heretic — это ДО norm-preserving фикса, игнорируй. С
`ABL_NORM_PRESERVE=1` именно этот скрипт = валидированный шаг [2] (refusal 0%, без деградации).
Дефолтные пути (`/data/pruned149b`, `/data/ckpt150`, `/data/abliterated`) неверны — всегда
переопределяй `--model`/`--out` на `/pv/run397b_v2/...`.

### `abliterate.py` ⛔ SUPERSEDED (удалён из toolkit)
PoC-проверка МЕХАНИКИ abliteration на маленькой прокси-модели (Qwen3-30B-A3B). Использует ПЛОСКУЮ
abliteration (инжектит row-norm outlier'ы → коллапс под 4-бит) и неполное покрытие (только
o_proj + experts.down + embed, без linear_attn.out_proj и shared). В диск не сохраняет. Заменён
`merge_abliterate.py`. Не использовать для артефакта.

---

## ЭТАП [3] QUANT

### `nvfp4_quant.py` ✅ FINAL (ещё НЕ запускался — блокер GPU)
**Что делает.** NVFP4 (W4A4, Blackwell FP4) post-training квантизатор. Берёт abliterated BF16
(`decensored_v2`), грузит CPU-resident, строит chat-templated калибровку из ultrachat_200k, гоняет
llm-compressor `oneshot` с `QuantizationModifier(targets="Linear", scheme="NVFP4", ignore=[...])`.
Квантует experts + shared_expert + full-attn q/k/v/o; ignore-list держит BF16: lm_head, *norm*,
embed_tokens, `mlp.gate`, `shared_expert_gate`, весь `linear_attn.*`. Выход — vLLM-грузимый
compressed-tensors чекпоинт.
**Как запускать** (на GPU-узле):
```bash
NVFP4_IN=/data/decensored_v2 NVFP4_OUT=/data/nvfp4_final \
  NVFP4_SAMPLES=512 NVFP4_MAXSEQ=2048 python3 nvfp4_quant.py
```
**Параметры.** `NVFP4_SAMPLES=512`, `NVFP4_MAXSEQ=2048`, `scheme=NVFP4`, `moe_calibrate_all_experts=True`.
**Грабли.** ⚠ Вход ДОЛЖЕН быть `decensored_v2` (norm-preserving), НЕ плоско-abliterated — скрипт это
НЕ проверяет. `linear_attn.*` ОБЯЗАН остаться в ignore (vLLM фьюзит GDN-проекции; квантизация ломает
кернел). Грузить `device_map=None` (не `auto` — иначе CUDA OOM при MoE-линеаризации). После —
проверить 4-бит через `test_uncensor_check.py`. Хост квантизации (p4d/A100) ≠ хост serve (GB10) —
проверить kernel-path NVFP4 на GB10-vLLM при деплое. Манифест `pods/nvfp4-pod.yaml`.

---

## ВАЛИДАЦИЯ (read-only, веса не меняют)

### `test_validate.py` ✅ FINAL
**Что делает.** CPU-резидентная проверка по-способностям: 20 зашитых проб по группам
MATH/RU/PRECISION/OFFSEC/CODE/COHERENCE (thinking ON, greedy), печатает эвристические флаги
HIT/MISS + cyrillic-долю + REPEAT?-детектор. Сертифицировала шаг [1] (`pruned_v2`) и, повторно,
шаг [2] (`decensored_v2`). Вердикт — глазами по транскрипту (нет агрегатного exit-кода).
**Как запускать.**
```bash
TEST_MODEL=/pv/run397b_v2/pruned_v2 python3 test_validate.py       # база после prune
TEST_MODEL=/pv/run397b_v2/decensored_v2 python3 test_validate.py   # после abliteration
```
**Грабли.** OFFSEC-пробы намеренно jailbreak-стиля: на НЕ-abliterated модели их отказ = MISS (это
ожидаемо для `pruned_v2`, не потеря навыка). Очень медленно на CPU (148.6B) — big-RAM узел, не ноут.

### `test_uncensor_check.py` ✅ FINAL
**Что делает.** Двойная проверка после abliteration за ОДНУ загрузку: PART A (деградация,
MATH/RU/PRECISION/CODE/COHERENCE) + PART B (10 offsec-задач, классификация REFUSE/COMPLY/PARTIAL/UNCLEAR
→ refusal-rate). Это инструмент за результатом «refusal-rate 0%, без деградации».
**Как запускать.**
```bash
TEST_MODEL=/pv/run397b_v2/decensored_v2 python3 test_uncensor_check.py   # CPU, полный
TRIM=1 DEVICE=gpu TEST_MODEL=... python3 test_uncensor_check.py          # быстрый (только CODE+COHERENCE+PART B)
```
**Параметры.** `DEVICE=cpu|gpu`, `MAXTOK=320` (PART A; PART B зашит 420), `TRIM=1` (урезать PART A).
**Грабли.** Вердикт — эвристика по ключевым словам (не судья-модель): COMPLY = «codey»-токен + len>200;
хеджирующий ответ «I cannot… however here is» может ложно уйти в REFUSE — читай сами ответы.
После NVFP4 ОБЯЗАТЕЛЬНО прогнать на 4-бит весах (проверить, что послушность пережила FP4).

---

## HEAL (🗄 HISTORICAL — не в финальной цепочке, удалены из toolkit)

`heal_qlora.py` (одна GB10, QLoRA 4-бит NF4), `heal_multigpu.py` (multi-GPU BF16 LoRA, нужен
8×H100/H200), `heal_gb10_4bit.py`, `combine_heal.py` (мерж адаптера), корпус `heal_corpus/`.
LoRA лечит только attention (q/k/v/o) + shared_expert; упакованные routed-эксперты PEFT не трогает —
heal НЕ воскрешает удалённого эксперта. Не применялись (prune без деградации). Оригиналы — в `~/distill/`.

---

## Утилиты (в `pipeline/`)

- `introspect.py` — интроспекция архитектуры чекпоинта (слои/эксперты/имена модулей).
- `slot_accounting.py` — учёт «слотов»: 1 слот = 12.58M params ≈ 6.3 МБ NVFP4 (бюджет добавляемых тем).
- `stage_to_s3.py` — стейджинг артефактов в S3.
