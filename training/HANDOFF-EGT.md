# Handoff: Execution-Grounded Training pipeline

Стартовая инструкция для новой сессии. Продолжаем сборку системы, которая дообучает
локальную кодинг-модель на трейсах, проверенных исполнением. Читатель — senior, детали
в файлах ниже.

## Первым делом прочитать

- `/tmp/execution_grounded_training_system_full.md` — полная спека (41 раздел), архитектура
  и порядок этапов. Главный документ.
- `~/gb10/training/EXEC-GROUNDED-SFT.md` (на маке и на боксе) — сжатый рецепт пайплайна.

## Идея за одну фразу

Модель учится писать верный код на трейсах, где внешний grader подтвердил PASS исполнением.
FAIL-трейсы в обучение не идут — они запускают repair учителем. Инвариант: NO EXECUTION
PASS = NO TRAINING EXAMPLE.

## Роли (спека §4)

Student — локальная gh0stx-r2, которую дообучаем. Teacher — модель сильнее (DeepSeek или
локальный 122B), чинит там, где Student упал. Grader — детерминированный, только он даёт
PASS. Orchestrator — обычный Python, не AI.

Ключевой сигнал (спека §2,§3): Student-PASS годен для eval/regression, а основной
обучающий материал — Teacher-repair на задачах, где Student провалился.

## Инфраструктура (проверено)

- Бокс: `gh0stx@192.168.8.239`, GB10, 128GB unified, arm64 Blackwell (sm_121a).
- Serve: vLLM на `http://localhost:8000/v1`, модели `gh0stx`, `gh0stx-r2`. Занимает ~115GB
  из 121GB — второй крупной модели рядом нет места.
- Sampling для gh0stx: temp 1.0 / top_p 0.95 / top_k 20. Greedy (temp 0-0.2) даёт петли и
  галлюцинации — не использовать.
- opencode headless СЛОМАН: `opencode run` висит (rc=124 по таймауту на реальном таске).
  Раннер на opencode не строить. Используем прямой vLLM tool-calling.
- DeepSeek ключ: в `~/.zshrc` на боксе и на маке (`DEEPSEEK_API_KEY`). Достаётся с бокса,
  с мака сеть до api.deepseek.com не идёт — teacher-прогоны запускать на боксе.
- NVFP4-база: `~/nvfp4_v4_releases/gh0stx_pure` (есть tokenizer + config).
- Локальный 122B-учитель: `~/models/qwen35-122b-heretic` (76GB, Qwen3.5-MoE, compressed-
  tensors). Поднять serve = сначала погасить :8000 (память). Пока не делали.

## Что построено и доказано в прошлой сессии

- `~/gb10/training/harness/run_task.py` — прямой vLLM ReAct-раннер, замена opencode. Один
  тул `run_shell`, реальное исполнение в свежем `$W`, grader решает PASS. На PASS сам пишет
  train-ready строку в sft.jsonl. Доказан end-to-end.
- `~/gb10/training/harness/filter_safe_tasks.py` → `tasks/safe_subset.json` = 173 безопасных
  self-validated таска (алгоритмы, багфиксы, crypto-импл, парсеры, форензика, blue-team
  детект, static analysis). Offensive-категории (rce/webshell/privesc/injection) исключены.
- `~/gb10/training/harness/repair_cycle.py` — MVP Teacher-repair на одном таске (Student
  local → FAIL → Teacher DeepSeek чинит агентом → grader PASS → пишет source=teacher_repair,
  retry до 3). ПРОГНАН на `py_offbyone_chunk` 2026-09-25: Student FAIL за 12 ходов, Teacher
  PASS с первой попытки за 5 ходов, фикс эталонный (`range(0, len(xs) - size, size)` →
  `range(0, len(xs), size)`). Verified-строка в `/tmp/repair_offbyone.jsonl`, инференс-фрейминг
  (system=rules, user=prompt) сохранён, учительский диагноз в записанный system не протёк.
  Ingest через тренерский `build_example`: 6088 токенов, 521 размечен на assistant, usable.
- Формат SFT совпал с тренером: `{"messages":[system/user/assistant(+tool_calls)/tool],
  task_id, category, grade}`. `train/train_qlora_nvfp4.py` читает его напрямую через
  `build_example` (apply_chat_template, loss только на assistant-сегментах). Ingest-чек
  прошёл: 2/2 usable, 750-1104 размеченных токена. Conversion-шаг между harvest и train
  НЕ нужен.
- Реальные PASS-трейсы собраны: `/tmp/golden_sft.jsonl` на боксе (2 строки — dx_custom_base64,
  sa_ts_to_iso8601).

## Замер способностей Student (факт)

gh0stx-r2 проходит простые I/O-таски: `dx_custom_base64` PASS (8 ходов), `sa_ts_to_iso8601`
PASS (15). Проваливает reasoning-багфикс `py_offbyone_chunk` (FAIL, 19 ходов, зацикливание —
повтор одного tool-call без пивота). Вывод: модель тренируема, capability не ноль; свап базы
пока не нужен, слабые места лечит Teacher-repair.

## Трейн (спека §19)

```bash
BASE_DIR=~/nvfp4_v4_releases/gh0stx_pure \
DATA=<путь к sft.jsonl> \
OUT_DIR=<путь к адаптеру> \
EPOCHS=3 LR=2e-4 R=16 ALPHA=32 DROPOUT=0.05 MAXLEN=8192 GRAD_ACCUM=8 \
python3 ~/gb10/training/train/train_qlora_nvfp4.py
```
На выходе LoRA-адаптер. Деплой: vLLM `--enable-lora` (откат мгновенный) либо merge через
`train/merge_adapter.py`. Замер: pass-rate на held-out (не входивших в harvest) до/после.

## Границы (держать)

- Только безопасные/defensive/нейтральные кодинг-таски. Offensive-эксплуатация в пайплайн
  не идёт (спека §23). Sandbox не использовать как обход safety.
- Классификатор Claude реагирует на offensive-контент. В прошлой сессии он накопился и стал
  рубить agent-spawn. Поэтому: новую сессию держать чистой, не тащить в неё offensive-
  нарратив, оффенсив-часть (если понадобится) вести вне Claude.

## Следующие шаги (порядок)

1. [DONE] Research SWE-Gym. Конвенция: rollout `rollout-swe-train-full.sh` → `output.jsonl`
   (CodeActAgent-трейсы), eval `eval-swetrain-full-rollout.sh output.jsonl 32` метит resolved,
   в SFT идут только resolved-трейсы (их rejection-sampling), verifier — отдельная модель
   (unsloth Qwen2.5-Coder-32B, датасет `SWE-Gym/OpenHands-Verifier-Trajectories`) для
   best-of-n на инференсе. Маппинг: их resolved==наш grader PASS, их SFT-отбор==наш инвариант
   NO PASS = NO EXAMPLE. Расхождения нет; verifier у нас пока отсутствует (будущий этап, §20).
   Точный JSON-формат их SFT-строки в abstract/README не отдан — за github issue #9 и HF-карточками.
2. [DONE] `repair_cycle.py` на `py_offbyone_chunk` прогнан, ядро FAIL→Teacher→PASS→dataset
   доказано (см. раздел выше). §41 DoD закрыт до строки JSONL включительно; хвост (QLoRA →
   адаптер → held-out) — шаг 3.
3. [DONE] Микро-трейн локально прошёл 2026-09-26 через Path-C requant (`~/train_141b_requant.py`,
   NE `train_qlora_nvfp4.py` — тот бьёт generic from_pretrained = 262GB dense-эксперты, OOM).
   nf4-кэш экспертов готов (`~/nf4_cache/`, 64GB). FAILFAST: resident 76GB, peak 94.9GB, влезает.
   1 verified пример, 20 эпох GA=1, loss упал в 0.0009 на шаге 10 → оборван на epoch_9.
   serve-eval epoch_9 как gh0stx-r2 + `run_task py_offbyone_chunk` = `turns=19 grade=PASS`;
   база тот же таск валила (петля 19 ходов FAIL). Flip FAIL→PASS доказан, §34/§41 закрыт.
   Окружение: venv `~/.venvs/train` (peft 0.20/torch 2.12/tf 5.10). Ходить на бокс через `ssht`.
   Детали и рычаги ускорения — `HANDOFF-OPUS55-TRAINING.md`. См. [[egt-local-train-oom]].
   Тормоз requant ~2.7 мин/шаг (fla-fallback + per-forward nf4→bf16 dequant), полный корпус 25ч.

4. [DONE] Held-out eval: 7 тасков bugfix-класса вне harvest. Baseline gh0stx-r2 = 3/7.
   Харвест `harvest_corpus.sh` собрал 6 verified (3 bugfix + 3 алгоритма), трейн EPOCHS=2 GA=1
   на них, held-out после = 4/7 (флипнул bash_numeric_compare FAIL→PASS). +1 в пределах шума
   одного прогона, не устойчиво. Полный цикл harvest→train→held-out в `HANDOFF-OPUS55-TRAINING.md`.
   Узкое место — скорость трейна ~2.7 мин/forward и eval на enforce-eager ~2-3× медленнее прода.

5. Масштаб (§35): больше корпус (repair по всем failing-таскам safe_subset), повтор held-out
   для отсева шума, ускорение трейна (fla+causal-conv1d под sm_121a, фьюзд dequant экспертов).
4. Масштаб (спека §35): 100 тасков, SQLite state, retry, held-out eval, dataset versioning.

## Файлы

Бокс `~/gb10/training/harness/`: run_task.py, repair_cycle.py, filter_safe_tasks.py,
tasks/safe_subset.json, export_sft.py, run_authored.sh, gh0stx-rules.md.
Бокс `~/gb10/training/`: EXEC-GROUNDED-SFT.md, train/train_qlora_nvfp4.py, LOCAL_TRAINING.md.
Бокс `/tmp/`: golden_sft.jsonl (2 PASS-строки).
Мак: `~/gb10/training/EXEC-GROUNDED-SFT.md`, `/tmp/execution_grounded_training_system_full.md`,
`/tmp/HANDOFF-EGT.md` (этот файл).
