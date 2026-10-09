# Execution-grounded SFT

Модель учится писать верный код на трейсах, где результат проверен исполнением, а не
на правдоподобных текстах. В SFT идёт только то, где grader подтвердил PASS. Это режет
галлюцинации: «похоже на правду» больше не равно «правда».

Основано на CTF-Dojo (модель решает в песочнице, наружу только execution-verified трейсы).
Применяется здесь к безопасным доменам: алгоритмы, багфиксы, crypto-импл, парсеры,
форензика, blue-team детект. Offensive-категории (rce/webshell/privesc/injection) в этот
пайплайн не идут.

## Стадии

Пять шагов, каждый следующий зависит от предыдущего.

1. Авторинг тасков — JSON с 4 bash-скриптами.
2. Валидация контракта — таск решаем и не сдаётся без решения.
3. Harvest — модель решает в песочнице, grader проверяет, PASS-трейс сохраняется.
4. Экспорт в SFT — трейс превращается в обучающий пример.
5. Трейн + деплой — LoRA на fused-базе, serve адаптера, замер.

## 1. Авторинг тасков

Файл `gb10/training/harness/tasks/<name>.json`, структура `{"tasks": [ ... ]}`. Один таск:

```json
{
  "id": "cipher_rot13_decode",
  "category": "custom-encoding",
  "setup_sh": "#!/usr/bin/env bash\nset -e\ncd \"$W\"\necho 'uryyb jbeyq' > input.txt",
  "prompt": "input.txt holds a ROT13-encoded line. Decode it, write plaintext to answer.txt.",
  "grader_sh": "#!/usr/bin/env bash\ncd \"$W\" || exit 1\ntest \"$(cat answer.txt 2>/dev/null)\" = 'hello world' && echo PASS || echo FAIL",
  "reference_solution_sh": "#!/usr/bin/env bash\nset -e\ncd \"$W\"\necho 'uryyb jbeyq' | tr 'A-Za-z' 'N-ZA-Mn-za-m' > answer.txt",
  "self_test_passed": false
}
```

`$W` — рабочая папка, харнесс создаёт свежую на каждый прогон, все скрипты внутри неё.
`grader_sh` печатает `PASS` или `FAIL` первым словом, считает результат сам, с эталонным
файлом не сверяется. Промпт называет входной и выходной файл явно.

## 2. Валидация контракта

Жёсткий контракт: `setup → grader` == FAIL, `setup → reference → grader` == PASS. Первое
проверяет, что без решения таск не сдан. Второе — что эталон решает и grader это ловит.
Только таски с обоими условиями получают `self_test_passed=true` и идут дальше.

Валидатор: `harness/generators/gen_bugfix.py` (и аналоги) прогоняют контракт при генерации.
Для рукописных тасков — обёртка `validate_tasks.py` (см. шаг ниже), кладёшь JSON, она
репортит, какие прошли и почему битые.

## 3. Harvest

`harness/run_authored.sh` — цикл по таскам: создаёт `$W`, гоняет `setup_sh`, запускает
модель с промптом в этой папке, ждёт завершения, гоняет `grader_sh`. На PASS вызывает
`export_sft.py`, трейс сессии пишется в `dataset/sft.jsonl`.

Блокер сейчас: раннер завязан на `opencode run` в headless, который висит (rc=124 по
таймауту на реальном таске, проверено 2026-09-25). Пока не заменён на прямой vLLM
tool-calling раннер, harvest ничего не соберёт.

Прямой раннер `run_task.py` (домен-агностик): цикл ReAct против vLLM :8000, один тул
`run_shell` со scope в `$W`, tool-calling API, temp 1.0/top_p 0.95/top_k 20, стоп по
финишу модели или лимиту ходов. Трейс сообщений отдаёт наружу для экспорта.

## 4. Экспорт в SFT

`export_sft.py` берёт трейс (system + user + ассистентские ходы с tool-call'ами +
результаты тулов) и пишет строку JSONL в chat-формате. Правило train-format=inference-format:
формат примера совпадает с тем, как модель зовётся на инференсе, иначе AT-провал
(см. sft-format-coverage-lesson).

## 5. Трейн + деплой

LoRA на fused-базе (ZeRO-3 bf16), ~3ч, рецепт в `LOCAL_TRAINING.md`. Serve адаптера через
vLLM `--enable-lora` (served-lora-recipe). Замер до/после на held-out тасках: pass-rate
на тех, что модель не видела в harvest.

## Что осталось построить

- `run_task.py` — прямой vLLM раннер вместо opencode (главный блокер).
- `validate_tasks.py` — обёртка контракта для рукописных тасков.
- Врезка safe-only фильтра в harvest (готов `filter_safe_tasks.py`, 173 таска).
