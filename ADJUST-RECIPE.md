# ADJUST-RECIPE — снятие отказов (шаг [2], norm-preserving abliteration)

> ВАЛИДИРОВАНО 2026-07-01: `pruned_v2` → `decensored_v2` (BF16), refusal-rate 0%, без деградации.
> Скрипт: `pipeline/merge_abliterate.py`. Проверка: `pipeline/test_uncensor_check.py`.
> Манифесты: `pods/uncensor-pod.yaml` (adjust) + `pods/validate-uncensor-pod.yaml` (проверка).

## 1. Задача

Сделать `pruned_v2` полностью инструкции-следующей для авторизованного внутреннего offsec-тестирования:
убрать единый «вектор отказа» (Arditi 2406.11717) из остаточного потока, НЕ повредив навыки и НЕ
подготовив почву для коллапса под 4-бит. Вход = `pruned_v2` НАПРЯМУЮ (HEAL пропущен — prune без деградации).

## 2. Ядро метода — norm-preserving biprojected ablation

- Обычная (плоская) abliteration `W -= outer(d, d@W)` проецирует направление отказа, но МЕНЯЕТ L2-норму
  каждой строки → инжектит row-norm **outlier'ы**. В BF16 это переживается, но под NVFP4 (W4A4)
  outlier'ы дают repetition-collapse. Это был корень ранней сломанной 4-бит сборки.
- **Фикс:** norm-preserving (grimjim biprojected / Heretic `RowNormalization.FULL`, `ABL_NORM_PRESERVE=1`)
  — после проекции ВОССТАНАВЛИВАЕТ исходную L2-норму каждой строки. Нет outlier'ов → NVFP4-safe.
- Прецедент: AEON-7 (abliterate → NVFP4 → serve на GB10).

## 3. Куда применяем (все писатели остаточного потока)

Стиль AEON-7 (attention-only в прошлый раз НЕ дожал — эксперты держали пути отказа):

| Цель | Кол-во | Тип |
|------|--------|-----|
| self_attn.o_proj | 15 | full-attn writer |
| self_attn.linear_attn.out_proj | 45 | GDN writer |
| experts.down_proj | 60×184 слайсов | ВСЕ routed-эксперты (TrevorS EGA) |
| shared_expert.down_proj | 60 | shared writer |
| embed_tokens | rows | эмбеддинг |

«Читатели» — q/k/v, gate_up_proj, роутер `gate`, нормы — НЕ трогаем.

## 4. Направление отказа

- Слой **36** (`int(0.6 × 60)`); pre-norm diff-norm ≈ **1.85**.
- Замер: float64-Welford среднего last-token hidden state по ~32 «вредным» vs ~32 «безвредным»
  chat-templated промптам; `d = normalize(mean_harmful − mean_harmless)`.
- `ABLITERATE_LAYER=auto` — слой с макс diff-norm в полосе глубины [0.25, 0.85]; или явное число.

## 5. Как запускать (воспроизведение decensored_v2)

Большой CPU-узел (GPU не нужен; грузим CPU-resident `device_map=None` — нужны реальные тензоры для
in-place правок):

```bash
ABL_NORM_PRESERVE=1 ABL_DO_OPROJ=1 ABL_DO_GDN_OUT=1 ABL_DO_EXPERT_DOWN=1 \
ABL_DO_SHARED_DOWN=1 ABL_DO_EMBED=1 ABL_BATCH=8 ABL_SHARD=20GB \
  python3 merge_abliterate.py --skip-lora \
    --model /pv/run397b_v2/pruned_v2 --out /pv/run397b_v2/decensored_v2
```

- `--skip-lora` ОБЯЗАТЕЛЕН: финальная цепочка НЕ мержила heal-LoRA. Без него скрипт полезет за
  адаптером в `/data/ckpt150` (historical-путь).
- `ABL_NORM_PRESERVE` держать **1**. `=0` — только для A/B-сравнения, для артефакта запрещено.

## 6. Грабли

- ⚠ Докстринг/inline-комментарии в `merge_abliterate.py` УСТАРЕЛИ: называют in-house abliteration
  «crude path that damaged the model» и советуют Heretic-handoff — это ДО norm-preserving фикса.
  С `ABL_NORM_PRESERVE=1` именно этот скрипт = валидированный шаг [2]. Верь коду, не комментариям.
- Heretic-инструмент на нашу модель НЕ идёт: ARM aeon-образ влезает на GB10, но 297 ГБ BF16 > 122 ГБ;
  x86 p4d влезает, но Heretic падает на weight-conversion → поэтому in-house `merge_abliterate.py` (CPU).
- Дефолтные пути (`/data/pruned149b`, `/data/ckpt150`, `/data/abliterated`) неверны — всегда `--model`/`--out`.
- Peft обойдён намеренно (краш peft 0.19.1 ↔ transformers 5.12.1); ручной merge важен только на LoRA-пути.

## 7. Проверка (двойная)

```bash
TEST_MODEL=/pv/run397b_v2/decensored_v2 python3 test_uncensor_check.py
```

- **PART A (деградация):** MATH/RU/PRECISION/CODE/COHERENCE — все ✅ (не пострадали vs `pruned_v2`).
- **PART B (послушность):** 10 offsec-задач → **COMPLY 10/10, refusal-rate 0%**.
- Вердикт PART B — эвристика по ключевым словам; читать сами ответы (хеджирующий «I cannot… however
  here is» может ложно попасть в REFUSE).
- ⚠ Это проверка BF16-артефакта. После NVFP4 (шаг [3]) ОБЯЗАТЕЛЬНО повторить на 4-бит весах — именно
  тот режим отказа, который предотвращает norm-preserving.

## 8. Артефакт

`decensored_v2` (BF16, 16 shards, 277 ГБ) → PVC `/pv/run397b_v2/decensored_v2` + S3
`s3://ai-monitoring-test-123456789012/decensored_v2/`.
