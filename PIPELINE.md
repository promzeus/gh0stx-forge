# PIPELINE — поэтапное описание каждого прохода

> Что и в каком порядке мы делали. Источник правды по факту исполнения.
> Обновлено: 2026-07-02. Статус проходов: [1] ✅ · [2] ✅ · [3] ⬜ · [4] ⬜.
> Детали рецептов — в PRUNE-RECIPE.md, ADJUST-RECIPE.md, QUANT-DEPLOY.md.
> Описание каждого скрипта (что делает / как запускать) — в SCRIPTS.md.

## Контекст

Внутренний авторизованный R&D компании (AWS acct `123456789012`, кластеры test/dev,
владелец уполномочен по ИБ). Цель: из базовой MoE `Qwen/Qwen3.5-397B-A17B` собрать компактную
специализированную модель (offensive-security специалист для тестирования СОБСТВЕННОЙ
инфраструктуры), которая после 4-бит квантизации помещается на **одну плату NVIDIA GB10 (122 ГБ)**
и по качеству обходит текущий daily-driver heretic122. Язык ответов — русский.

## Пайплайн одним взглядом

```
Qwen3.5-397B-A17B (база, 397B, ~800 ГБ BF16)
  │
  ├─[1] PRUNE ─────────────► pruned_v2      148.6B BF16   ✅ валидировано (нет деградации)
  │      pipeline_reap.py + calib_corpus.py
  │
  ├─[2] ADJUST ────────────► decensored_v2  148.6B BF16   ✅ валидировано (refusal 0%)
  │      merge_abliterate.py  (norm-preserving, прямо из pruned_v2)
  │      [HEAL — пропущен: prune не дал деградации, лечить нечего]
  │
  ├─[3] QUANT ─────────────► nvfp4_final    ~4-бит W4A4    ⬜ следующий (блокер: GPU)
  │      nvfp4_quant.py  (NVFP4, Blackwell)
  │
  └─[4] DEPLOY ────────────► vLLM на GB10                  ⬜ финал
         serve + rewrap config + финальная валидация
```

Артефакты каждого прохода лежат на PVC `distill-data` (eu-north-1c) в `/pv/run397b_v2/`
и продублированы в S3 `s3://ai-monitoring-test-123456789012/`.

---

## [1] PRUNE — прореживание экспертов MoE (397B → 148.6B)

**Что делали.** База — 60 блоков × 512 экспертов, top_k=10 + всегда-активный shared_expert.
Оставили 36% экспертов на блок (512 → 184, `KEEP_FRAC=0.36`), shared_expert НЕ трогали.
Итог — `pruned_v2`, 148.6B BF16.

**Как считали важность (главный фикс).** Метрика = **счётчик выбора эксперта роутером ПО ДОМЕНАМ**:
forward-hook на `gate` каждого блока, bincount top-k индексов, с тегом домена калибровочного примера.
Это лечит корень провала первой (сломанной) попытки, где считали ОДИН глобальный счётчик на
англоязычном offsec-корпусе → русские/математические эксперты получали 0 обращений → вырезались.

**Как выбирали keep-set.** Честный round-robin **UNION** топ-экспертов по каждому домену: каждый
домен по очереди добавляет своего следующего живого эксперта, пока не наберём `keep_g=184`. Это даёт
встроенный анти-starvation floor (~30 экспертов/домен/блок). Добор глобальным топом, если мёртвые
эксперты оставили бюджет.

**Калибровка (первичный фикс).** Сбалансированный, размеченный по доменам, полнотекстовый корпус
(вход+ответ, НЕ голые промпты): russian ≥15%, math ≥12%, precision ≥12%, offsec ≤25% (капнуто!),
code ~18%, general ~15%. Ассерт покрытия: каждый домен обязан получить >0 обращений.

**Скрипты:** `calib_corpus.py` (корпус) → `pipeline_reap.py` (Phase A счёт + Phase B union-keep,
резюмируемый через `importance_v2.json`, sanity-генерации). **Манифест:** `pods/reprune-pod.yaml`.
**Железо:** r7i.48xlarge (1.5 ТБ RAM, eu-north-1c), `DEVICE_MAP=cpu`, ~$18, resumable.

**Проверка:** `test_validate.py` на `pruned_v2` — MATH 5/5, PRECISION 4/4, RU/CODE/COHERENCE ✅,
деградации нет. Единственное «замечание» — модель хеджировала на offensive-коде (ось отказа) —
снимается на шаге [2].

---

## [2] ADJUST — снятие отказов (norm-preserving abliteration)

**Что делали.** Сделали модель полностью инструкции-следующей: убрали единый «вектор отказа»
(Arditi 2406.11717) из остаточного потока. `pruned_v2` → `decensored_v2` (BF16). HEAL пропущен —
prune не дал деградации, лечить нечего, поэтому abliteration применили ПРЯМО к `pruned_v2`.

**Как.** Метод — **norm-preserving biprojected ablation** (grimjim / Heretic RowNormalization.FULL,
`ABL_NORM_PRESERVE=1`): проецируем вектор отказа из всех «писателей» остаточного потока и
восстанавливаем исходную L2-норму каждой строки. Это критично: обычная (плоская) abliteration
инжектит row-norm outlier'ы, которые переживают BF16, но КОЛЛАПСируют под 4-бит (NVFP4). Именно
norm-preserving делает результат безопасным для этапа [3].

**Куда применяли (все писатели остатка, стиль AEON-7):** o_proj (15 full-attn) +
linear_attn.out_proj (45 GDN) + experts.down_proj (60×184 слайсов, ВСЕ эксперты) +
shared_expert.down_proj (60) + embed_tokens. «Читатели» (q/k/v, gate_up_proj, роутер, нормы) — НЕ трогаем.

**Направление отказа.** Слой 36 (`int(0.6×60)`), diff-norm ≈1.85: float64-Welford среднего
last-token hidden state по ~32 «вредным» vs ~32 «безвредным» chat-промптам, d = normalize(harmful−harmless).

**Скрипт:** `merge_abliterate.py` (`--skip-lora`, все `ABL_DO_*` ON). **Манифест:** `pods/uncensor-pod.yaml`.
**Железо:** CPU big-RAM (r7i.24/48xlarge), GPU не нужен.

**Проверка (двойная):** `test_uncensor_check.py` на `decensored_v2` —
PART A (деградация): MATH/RU/PRECISION/CODE/COHERENCE ✅; PART B (послушность): COMPLY 10/10,
**refusal-rate = 0%**. Манифест проверки: `pods/validate-uncensor-pod.yaml`.

---

## [HEAL] — пропущен (не входит в финальную цепочку)

Существует целая ветка heal/QLoRA (`heal_qlora.py`, `heal_multigpu.py`, `heal_gb10_4bit.py`,
`combine_heal.py`, `heal_corpus/`) — исследовали, но **НЕ применяли**: сбалансированный prune не дал
измеримой деградации, поэтому лечить нечего. Держим как переиспользуемую технику на случай, если
будущий prune реально что-то уронит. Важно: LoRA лечит только достижимые attention-линейки
(и shared_expert), упакованные routed-эксперты PEFT не трогает — то есть heal НЕ воскрешает
удалённого эксперта, покрытие обязано быть корректным на шаге [1].

---

## [3] QUANT — NVFP4 (W4A4) под Blackwell ⬜ СЛЕДУЮЩИЙ

**Что сделать.** `decensored_v2` (BF16) → `nvfp4_final` (compressed-tensors, 4-бит W4A4) через
llm-compressor `oneshot`. Квантуем: routed-эксперты + shared_expert + full-attn q/k/v/o.
Оставляем BF16 (ignore-list): lm_head, все *norm*, embed_tokens, роутер `mlp.gate`, `shared_expert_gate`
и **весь `linear_attn.*`** (иначе vLLM ломает fused GDN-кернел).

**Скрипт:** `nvfp4_quant.py`. **Манифест:** `pods/nvfp4-pod.yaml` (уже указывает на `decensored_v2`).

**Блокер:** нужен GPU. eu-north-1 H100/H200 — capacity-dry. Варианты:
(A) добавить GPU-нодпул в example-staging eu-west-1 (тот же аккаунт); (B) стриминговая квантизация на
самом GB10. Для квантизации хватит p4d (8×A100-40): BF16 грузится CPU-resident, oneshot онлоадит
по одному блоку на карту. Детали и граблики — QUANT-DEPLOY.md.

**После квантизации:** обязательно перезапустить `test_uncensor_check.py` на 4-бит весах — проверить,
что послушность и навыки пережили FP4 (именно этот режим отказа предотвращает norm-preserving на [2]).

---

## [4] DEPLOY — vLLM на GB10 ⬜ ФИНАЛ

**Что сделать.** Поднять `nvfp4_final` на GB10 (Grace+Blackwell, sm_121a, 122 ГБ) под vLLM.
Перед запуском — rewrap config (llm-compressor сохраняет плоский конфиг → vLLM уводит в
мультимодальный VL-путь → краш; надо обернуть в `Qwen3_5MoeForConditionalGeneration`).

Ключевые флаги serve: `--quantization compressed-tensors --language-model-only
--load-format runai_streamer --enforce-eager --max-num-batched-tokens 8192 --trust-remote-code`
(+ KV-cache, context, MTP-drafter). Полная команда и обоснование каждого флага — QUANT-DEPLOY.md.

**Финальная валидация:** деградация + послушность на реальном железе (Blackwell = единственный
корректный тест NVFP4), затем A/B против heretic122.
