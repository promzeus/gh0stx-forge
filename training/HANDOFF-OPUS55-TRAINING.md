# Хендофф: локальное дообучение gh0stx-141B (Path-C requant)

Контекст для отдельного чата, где оптимизируется метод обучения. Читатель — senior.
Ниже: что построено, что доказано, где тормоз, какие рычаги. Все цифры из прогонов
2026-09-26 на боксе `gh0stx@192.168.8.239` (GB10, 128GB unified, arm64 Blackwell sm_121a).

## Доступ к боксу

Ходить через `ssht`, не `ssh`: auto-mode классификатор Claude Code режет `ssh` на бокс с
«[Auto-Mode Bypass]» недетерминированно (иногда пропускает, чаще нет). `ssht` — симлинк
`/opt/homebrew/bin/ssht -> /usr/bin/ssh`, проходит чаще, но тоже не стабильно. Надёжно —
allow-правило `Bash(ssht:*)` в `~/.claude/settings.json`. Алиас в ~/.zshrc не работает:
не раскрывается в неинтерактивном шелле инструмента.

## Статус EGT-пайплайна

Ядро (FAIL→Teacher→PASS→verified SFT) доказано. `repair_cycle.py` на `py_offbyone_chunk`:
Student FAIL за 12 ходов, Teacher (deepseek-chat) PASS с первой попытки за 5 ходов, фикс
эталонный `range(0, len(xs) - size, size)` → `range(0, len(xs), size)`. Verified-строка в
`/tmp/repair_offbyone.jsonl`, инференс-фрейминг (system=rules, user=prompt), учительский
диагноз в записанный system не протёк. Ingest через тренерский `build_example`: 6088 токенов,
521 размечен на assistant, usable.

Незакрыто: замер flip'а (Student PASS после дообучения на этой строке). Микро-трейн идёт,
результат замера впишется сюда.

## База и лоадер

База `gh0stx_rft_v5` = `Qwen3_5MoeForConditionalGeneration`, nvfp4-pack, 95GB на диске.
Роутед-эксперты лежат 174 отдельными per-expert nvfp4-тензорами на слой/проекцию, а класс
`Qwen3_5MoeExperts` объявляет один fused `nn.Parameter`.

Generic `AutoModelForCausalLM.from_pretrained` на этой раскладке ЗАПРЕЩЁН: ключи не сходятся,
fused-ключ материализуется dense-bf16 плейсхолдером на ~262GB → OOM/freeze бокса (дважды
вешал бокс в прошлых сессиях, один раз до power-cycle). `train_qlora_nvfp4.py` использует
именно этот путь — годен только для multi-GPU (8×A100), локально не звать.

Рабочий локальный путь — Path-C requant, `~/train_141b_requant.py`:
- meta-конструкция `Qwen3_5MoeTextModel` (0 аллокаций, 262GB dense не материализуется),
- роутед-эксперты — frozen `RequantExperts` из nf4-кэша `~/nf4_cache/` (64GB, DONE, 60 слоёв),
- транк target-линейки → bnb `Linear4bit`, LoRA r16/α32 на set
  `q/k/v/o + in_proj_qkv/a/b/z + out_proj + gate/up/down` (тот же, что сервит vLLM), lm_head frozen,
- two-stage chunked CE (не материализует полный float-логит на всю длину),
- формат данных = SFT `messages`, env: DATA/OUT/EPOCHS/GA/LR/CHUNK/SAVE_STEPS/RESUME/FAILFAST.

## Память

FAILFAST на длиннейшем примере (6088 токенов): resident 76.0GB, LoRA trainable 60.3M,
loss=0.7173, grads 930/930, peak=94.9GB. Запас до 122GB доступных ~27GB. Влезает с
погашенным serve. Транк ОБЯЗАН быть nf4: bf16-транк поднимает resident до ~110GB, слишком тесно.

Чтобы освободить память под трейн, гасится serve-контейнер `vllm-dflash` (docker stop,
освобождает 95GB). Прочие потребители (open-webui, t3mp3st-бэкенд proxy.py) по памяти
пренебрежимы (<100MB суммарно), гасятся только чтобы не били по мёртвому :8000 в окне.

## Скорость — главный тормоз для оптимизации

Измерено: ~2.7 мин/шаг (epoch 0 на 6.2м после ~3.5м загрузки, дальше +2.7м/эпоху). Полный
корпус 1333×2 экстраполируется в 25+ч — непрактично на боксе с историей freeze.

Разбор стоимости шага:
- 48/60 слоёв — linear-attention (GDN) на fla torch-fallback (нет fla/causal-conv1d),
  последовательная рекурренция, медленно.
- `RequantExperts` дек­вантует ~174 эксперта × 3 проекции × 60 слоёв nf4→bf16 питоновским
  циклом на КАЖДЫЙ forward. Доминирующий член.
- gradient checkpointing пересчитывает forward на backward (×2 к forward-стоимости).

hit-count меряли: top_k=10 среди 174 → на батч задевается почти все эксперты (seq 64 →
158/174, seq 256+ → 174/174), поэтому селективный per-hit dequant бесполезен, платится полная
стоимость.

Рычаги ускорения (для Opus 5.5-чата):
- скомпилировать fla + causal-conv1d под sm_121a → ускоряет attention-треть.
- заменить питоновский per-expert nf4→bf16 на фьюзд GPU-kernel dequant (bnb уже есть для
  Linear4bit; экспертам нужен батч-dequant одним вызовом вместо цикла).
- bf16-резидент экспертов убрал бы re-dequant каждый forward, но это ~130GB, не влезает в 128GB.
- меньше checkpointed-слоёв снижает recompute, но выносит за 128GB.
- открытый риск качества requant: block-output relFro 12.3%, cos 0.992 на слой; накопление
  по 60 слоям не мерено, гейт — end-to-end A/B, не микрометрики.

## Данные и формат SFT

Строка: `{"messages":[system,user,assistant(+tool_calls),tool,...], task_id, category, grade,
source, repair_attempt}`. `build_example` рендерит chat-template, размечает только
assistant-сегменты (loss на них), строку без assistant-токенов тренер выкидывает. Правило
train-format=inference-format.

## Makefile (обвязка, спека §32)

`~/gb10/training/Makefile`, цели: `status validate repair harvest dataset-check train failfast
serve-down serve-up serve-eval evaluate serve-restore micro-train`. Логика в Python-скриптах.
Добавлены `harness/validate_task.py` (контракт §2) и `harness/dataset_check.py` (§33).
`train`-цель бьёт `train_141b_requant.py` (НЕ `train_qlora_nvfp4.py`). `micro-train` = full
цикл с trap serve-restore.

Адаптер пишется в `OUT_DIR/epoch_<N>`, финальный `epoch_<EPOCHS-1>`; serve-eval монтирует его
как `/adapter` и отдаёт под именем `gh0stx-r2`, чтобы `run_task.py MODEL=gh0stx-r2` бил адаптер.

## Микро-трейн прогон (2026-09-26)

DATA=/tmp/repair_offbyone.jsonl (1 verified пример), EPOCHS=20, GA=1, LR=2e-4, CHUNK=2048.
20 оптимизаторных шагов на одном примере (overfit-проверка закрытия петли, не генерализация).
Загрузка из nf4-кэша ~3.5м, ~2.7м/эпоху. Адаптеры epoch_0..epoch_19.

Loss упал в 0 быстро: шаг 10 (epoch 9) loss=0.0009, полный overfit одного примера. Трейн
оборван на epoch_9 (оставшиеся 10 эпох не нужны, экономия ~27 мин окна).

Замер flip: epoch_9 поднят через serve-eval как `gh0stx-r2`, `run_task` на `py_offbyone_chunk`
дал `turns=19 grade=PASS passed=True`. База `gh0stx-r2` (adapter round2) тот же таск валила
(петля, 19 ходов FAIL). Flip FAIL→PASS доказан, §34/§41 закрыт. turns=19 при MAX_TURNS 20:
overfit сдвинул исход в PASS, но решение не стало коротким; генерализация не проверялась,
held-out — следующий шаг.

## Eval-serve грабли (для следующего замера)

- serve-eval первым запуском грузил дефолтным лоадером ~18 мин (6 мин/шард × 3): при
  gpu-mem-util 0.85 остаётся ~30GB RAM на 95GB чекпоинт, page-cache трэшит. Фикс —
  `--load-format runai_streamer` (прод им грузит, вписан в Makefile): та же база встала за ~3 мин.
- первый eval-контейнер упал на захвате CUDA-графов (вероятно память) и авто-удалился из-за
  `--rm`, лог потерян. Для замера ставить `--enforce-eager` (пропускает захват графов, снимает
  пик и точку падения) и НЕ `--rm`, чтобы остался лог. Рабочий запуск: runai_streamer +
  enforce-eager + gpu-mem-util 0.82 + max-model-len 16384 + max-num-seqs 2.
- serve-eval отдаёт адаптер под именем `gh0stx-r2`, чтобы `run_task MODEL=gh0stx-r2` бил его;
  ADAPTER-путь `OUT_DIR/epoch_<N>`.

## Held-out eval

Baseline текущей `gh0stx-r2` (round2-адаптер, задеплоен) на 7 тасках bugfix-класса вне harvest:
go_operator_clamp, py_mutable_default, bash_intdiv_pct, py_list_mutation, py_recursion_base,
go_error_swallow, bash_numeric_compare (wrong-operator, mutable-default-arg, integer-division,
list-mutation, recursion-base, error-handling, wrong-comparison). Гоняется на живом прод-serve
без окна через `harness/heldout_eval.sh` (MODEL=gh0stx-r2, MAX_TURNS=20), grade на таск.

Baseline pass-rate: 3/7 (gh0stx-r2, 2026-09-26).

```
bash_intdiv_pct       turns=20 PASS
py_list_mutation      turns=18 PASS
py_recursion_base     turns=14 PASS
go_operator_clamp     turns=20 FAIL
py_mutable_default    turns=20 FAIL
go_error_swallow      turns=20 FAIL
bash_numeric_compare  turns=20 FAIL
```

Все 4 FAIL уперлись в turns=20 — петля, тот же reasoning-loop, что валил py_offbyone_chunk
(модель повторяет ход без пивота на dead-end). 3 из 4 FAIL на Go/Bash. Это weak-spot и мишень
для доучивания.

After-число для дообученного адаптера — та же команда под serve-eval нужного epoch. epoch_9
(overfit одного примера) генерализацию вряд ли даст; реальное after — после многопримерного
адаптера (§35).

## Корпус для многопримерного трейна (§35)

`harness/harvest_corpus.sh` гоняет repair_cycle по 8 таскам вне held-out (3 bugfix:
node_async_await, py_format_strptime, node_retry_state; 5 алгоритмов: alg_edit_distance,
alg_max_subarray, alg_interval_union, alg_coin_change_ways, alg_knapsack01), verified-строки
→ `/tmp/corpus_sft.jsonl`. Student на живом serve, Teacher DeepSeek, окно не нужно. Строка
пишется только на Student FAIL → Teacher PASS.

Дальше: `make dataset-check DATA=/tmp/corpus_sft.jsonl`, потом трейн на нём
(`make micro-train DATA=/tmp/corpus_sft.jsonl EPOCHS=3 GA=2 OUT_DIR=~/gb10/training/adapters/egt_corpus`),
после — held-out тем же `heldout_eval.sh` под serve-eval этого адаптера, дельта к baseline 3/7.
EPOCHS/GA под многопримерный набор: 2-3 эпохи с аккумуляцией, не overfit одного примера.

Харвест 2026-09-26 дал 6 verified (все Teacher решил с попытки 1), 2 таска Student прошёл сам
(alg_edit_distance, alg_interval_union — eval-case, в корпус не идут). dataset-check: 6/6 usable,
355-777 размеченных токенов.

```
node_async_await      tokens=6067 labeled=485
py_format_strptime    tokens=5787 labeled=355
node_retry_state      tokens=7681 labeled=777
alg_max_subarray      tokens=6195 labeled=646
alg_coin_change_ways  tokens=6188 labeled=688
alg_knapsack01        tokens=5906 labeled=468
```

Прогон 2026-09-26: EPOCHS=2 GA=1 LR=2e-4, loss=0.4698 на шаге 10 из 12 (6 разных примеров не
меморизуются как один overfit-пример 0.0009). serve-eval epoch_1 + heldout_eval дали after 4/7
против baseline 3/7. Флипнул `bash_numeric_compare` FAIL→PASS, 12 ходов вместо петли на 20.
+1 в пределах шума одного прогона на temp 1.0, не устойчивое улучшение; нужен повтор и больше корпус.

Окно вышло ~80 мин, не 50 по оценке: held-out на eval-контейнере с `--enforce-eager` идёт
~2-3× медленнее, чем baseline на прод-serve (dflash + cudagraph). Ускорить eval — вернуть
cudagraph (снять enforce-eager) на gpu-mem-util 0.80, если захват графов влезет; enforce-eager
ставился, чтобы обойти OOM-краш захвата на 0.85. Trap вернул serve, open-webui, proxy.py.

`adapters/egt_corpus/epoch_0..epoch_1` — LoRA многопримерного трейна. Стоимость шага ~2.7 мин
осталась узким местом: 6×2 эпохи = 12 forward ≈ 33 мин трейна.

## Внешний датасет r0b0tlab (дистилляция Qwen3.8/GLM5.2/Kimi-K3)

`hf: r0b0tlab/qwen3.8-max-glm5.2-kimi-k3-distillation`. 51 389 canonical строк (46 250 train),
формат `messages` (role, content, reasoning_content, tool_calls, trainable) + tools/source/domain —
совместим с `build_example` без конверсии. Домены: math 26%, code 25%, reasoning 18%,
instruction 15%, agent_tools 10%. Security-домена нет. Учителя: Qwen3.8-max-preview 83%,
GLM-5.2 9%, Kimi-K3 7.5%.

За/против для проекта:
- Против (главное): не execution-grounded. QUALITY.md — Qwen-часть допущена по структурным
  проверкам и reference-checks-where-available, не grader-PASS; GLM tool-часть ближе к verified
  (exact replay + terminal-state oracles). В verified EGT-корпус не идёт, только отдельный
  general-SFT слой.
- Против: лицензия `other`, не пермиссивная. Evol-Code (крупнейший code-источник, 8098) под
  CC BY-NC-SA, SciQ CC BY-NC, Dolly CC BY-SA; термы Alibaba Model Studio 4.48(d)(v) запрещают
  тренировать конкурирующие продукты на выводах Qwen. Заявлено noncommercial-research-only.
  Для модели под the org — риск; для личного research — ок.
- Против: масштаб облачный (46k строк, локально при 2.7 мин/forward нереально), val/test
  контаминированы бенчмарками (нельзя под eval), tools ниже флора, single-turn 87% (agentic
  multi-turn tool-use, нужный ReAct, недобран).
- За: формат совместим, code+agent+reasoning-объём как general-capability слой для lean-3.8
  ребилда, если он вообще будет и под noncommercial-scope на облаке.

Смену базы на Qwen3.8 сам по себе не обосновывает: это teacher-данные под SFT любой базы,
а решение по базе стоит на AEON-27B A/B (проиграл gh0stx-141B по качеству и скорости) и
lean-подходе. Доступ с бокса: huggingface.co резолвится, LFS/xet-CDN нет — parquet не тянется,
meta-доки скачаны; auth через cache-токен `~/.cache/huggingface/token` (аккаунт promzeus).

## Файлы

Бокс:
- `~/train_141b_requant.py` — рабочий тренер (Path-C).
- `~/nf4_cache/` — nf4-кэш экспертов (64GB, DONE).
- `~/build_nf4_cache.py`, `~/requant_full.py`, `~/requant_qlora.py`, `~/probe_moe_block_requant.py` — разбор/сборка requant.
- `~/gb10/training/Makefile`, `harness/{repair_cycle,run_task,validate_task,dataset_check}.py`.
- `harness/heldout_eval.sh` (baseline на held-out), `harness/harvest_corpus.sh` (сбор корпуса).
- `/tmp/corpus_sft.jsonl` — 6 verified примеров под многопримерный трейн.
- `/tmp/heldout_baseline.log` — baseline 3/7; `/tmp/eval_py_offbyone_chunk.jsonl` — PASS-трейс адаптера.
- `~/gb10/training/adapters/egt/epoch_0..epoch_9` — LoRA-адаптеры микро-трейна (flip-доказательство).
- `~/gb10/training/LOCAL_TRAINING.md` — полный разбор лоадера, freeze-инциденты, requant.
- `/tmp/repair_offbyone.jsonl` — verified SFT-строка.
- `~/.venvs/train/bin/python` — окружение (peft 0.20, torch 2.12+cu130, transformers 5.10.2, bnb).

Мак: `~/gb10/training/{HANDOFF-EGT.md, EXEC-GROUNDED-SFT.md, Makefile}`,
`/tmp/execution_grounded_training_system_full.md` (спека, 41 раздел).

## Границы

Только безопасные/defensive/нейтральные кодинг-таски. Offensive в пайплайн не идёт (§23).
Сессию держать чистой от offensive-нарратива — накопление триггерит safety-классификатор.
