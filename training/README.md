# gh0stx training

RFT→QLoRA обучение локальной модели `gh0stx` (Qwen3.5-397B, pruned ~141B, NVFP4) на
собственных задачах. Модель гоняет графические задачи, автоматический грейдер судит
PASS/FAIL, PASS-траектории уходят в SFT-датасет, QLoRA впечатывает их в веса.

Два слоя обучения:
- **правила** (`harness/gh0stx-rules.md`) — грузятся в каждый запрос через opencode
  `instructions`, действуют сразу, живут в промпте;
- **веса** (QLoRA) — собранные PASS-траектории дообучают модель, навык остаётся в весах.

## Игроки

- **gx10** — бокс GB10 (192.168.8.239, `ssh gh0stx@192.168.8.239`). Держит vLLM
  (`gh0stx` на `localhost:8000`), opencode и харвест-харнесс `~/octest/night/`. WiFi
  джиттерит — все долгие прогоны detached (`nohup setsid ... & disown`), статус через
  sentinel-файлы и лог.
- **EKS example-cluster** — кластер для QLoRA (аккаунт example-stage `123456789012`, eu-north-1,
  ns `distill`). Karpenter, GPU-ноды с нуля. `aws --profile example-stage`, kubeconfig
  `aws eks update-kubeconfig --name example-cluster`.
- **S3** — `ai-monitoring-test-123456789012` (eu-north-1, доступ из пода через Pod Identity
  SA `ai-monitoring-test`). Датасет `gh0stx-rft/sft.jsonl`, база `decensored_v4/`,
  адаптер `gh0stx-rft/adapter`.

## Раскладка

- `harness/` — авторинг задач и харвест: `build_tasks*.py`, `emit_from_json.py`,
  `microservices_security_dataset.json` (задачи-векторы), `run_authored.sh` (харвестер),
  `export_sft.py` (сессия opencode → SFT jsonl), `authored_tasks.json` (текущий набор),
  `gh0stx-rules.md` (правила).
- `data/` — снапшот `sft.jsonl` (сырой) и `sft.clean.jsonl` (после фильтра).
- `train/` — QLoRA: `train_qlora.py`, `entrypoint.sh`, `preflight.py`, k8s-манифесты
  (`nodeclass-l40s.yaml`, `job-qlora-l40s-any.yaml`), nvfp4-вариант.

## Шаг 1. Задача

Задача в `authored_tasks.json["tasks"]` (и в наборах-источниках вроде
`microservices_security_dataset.json`) — объект:

```
id, category, behavior,
setup_sh              # строит фикстуры в $W (рабочая директория задачи)
prompt                # что даём модели; ответ пишется в RESULT.txt
grader_sh             # PASS/FAIL: считает ожидаемое из фикстур, сверяет с $W/RESULT.txt
reference_solution_sh # эталон: setup -> reference -> grader должен дать PASS
self_test_passed      # true только после реального self-test
```

Правило: `self_test_passed:true` ставится не на глаз, а после прогона
`setup_sh → reference_solution_sh → grader_sh` (юзер ставит его авансом — не верить,
перепроверять). Грейдер и эталон должны сходиться по построению.

Каждый self-test гоняется в директории с подчёркиваниями в пути (как на боксе:
`au_<tid>_<pid>_<rand>`), потому что задачи вроде command-injection на `split("_")`
ломаются, если тестировать в чистом `/tmp`.

## Шаг 2. Добавить и провалидировать

Новые задачи из JSON-файла → self-test → дозапись PASS в `authored_tasks.json`:

```
# на боксе, ~/octest/night/
python3 emit_from_json.py <new_tasks.json> authored_tasks.json
```

`emit_from_json.py` прогоняет каждую задачу (setup→reference→grader), печатает PASS/FAIL,
дописывает только PASS (self_test_passed=true), пропускает уже существующие id.

Генераторы задач-семейств: `build_tasks.py` (crackme, ECDSA nonce-reuse, XOR-crib, Go-fix),
`build_tasks2.py` (repeated-key XOR, JWT weak-secret, SQLi, format-string SSTI, basic-auth
carve). Запуск `python3 build_tasks.py` = self-test, `--emit authored_tasks.json` = дозапись.

## Шаг 3. Харвест

`run_authored.sh` крутит round-robin по всем `self_test_passed` задачам до дедлайна: строит
фикстуры, гоняет `opencode run -m gx10/gh0stx` в `$W`, ждёт завершения (по стабилизации
частей сессии в SQLite), грейдит, на PASS экспортирует сессию через `export_sft.py` в
`dataset/sft.jsonl`. Список задач материализуется при старте — после добавления задач
харвестер надо перезапустить.

```
# на боксе, ~/octest/night/
pkill -f run_authored.sh; pkill -f "opencode run"; sleep 2
DL=$(( $(date +%s) + 10800 ))                    # дедлайн +3ч
nohup setsid bash run_authored.sh "$DL" >/tmp/authored.out 2>&1 </dev/null & disown

# мониторинг
tail -f authored.log                             # <время> <tid> t=<с> grade=PASS/FAIL collected=<n>
grep -m1 materialized /tmp/authored.out          # "<N> authored tasks materialized"
wc -l dataset/sft.jsonl

# стоп
touch STOP_AUTH                                   # харвестер выйдет после текущей задачи
```

Каждая PASS-траектория = `{system=rules, user=prompt, assistant(reasoning+tool_calls),
tool=results, ...}`. Тяжёлые задачи (крипто-атаки, web-эксплойт) часть уходит в FAIL —
харвестится только PASS, сам расклад PASS/FAIL показывает, что модель тянет сама.

## Шаг 4. Чистка датасета

Перед трейном сырой `sft.jsonl` фильтруется: отбрасываются строки <3 сообщений, без user,
без финального assistant-ответа, точные дубли. Битые экспорты (только system-промпт, сессия
не отработала) — основной мусор. Результат `sft.clean.jsonl` заливается в
`s3://ai-monitoring-test-123456789012/gh0stx-rft/sft.jsonl`.

## Шаг 5. QLoRA на EKS

База `decensored_v4` (bf16, 282GB, 16 шардов; = `promzeus/gh0stx-bf16`) в S3 и на PVC.
Роутинговые эксперты MoE — 3D `nn.Parameter`, bnb 4-bit их не квантует → остаются bf16
(~263GB), модель ~271GB. p4d (8×A100-40=320GB) забивается под завязку → OOM на обучении.
Нужен узел >320GB: **L40S g6e.48xlarge** (8×48=384GB) или H200 p5 (1128GB). LoRA-таргет —
attention (q/k/v/o) + linear_attn + shared_expert; роутинг-эксперты frozen (регекс в
`train_qlora.py`), поэтому unused-expert-градиентов нет.

Баг transformers 5.12.1: упаковщик экспертов детерминированно не собирает `gate_up_proj`
для 5 слоёв (15,24,33,42,51) этого чекпоинта. Обойдён патчем
`_move_missing_keys_from_meta_to_device` в `train_qlora.py` — вручную упаковывает эти
эксперты из чекпоинта до `dispatch_model`.

Ёмкость GPU в eu-north-1 плавает (spot placement score 1 = дефицит). Пул `gh0stx-l40s`
развязан от AZ: свой `nodeclass-l40s.yaml` (root 460Gi), нода в 1a или 1b, база синкается
из S3 на локальный диск. G on-demand квота поднята до 200 vCPU (g6e.48xlarge = 192).

```
export AWS_PROFILE=example-stage AWS_REGION=eu-north-1
aws eks update-kubeconfig --name example-cluster

# один раз: пул + nodeclass
kubectl apply -f train/nodeclass-l40s.yaml

# запуск (retry-луп: пока нет ёмкости — нода не встаёт, биллинга нет; появится — трейн сам)
kubectl apply -f train/job-qlora-l40s-any.yaml
kubectl -n distill get pods -l app=gh0stx-qlora -o wide
kubectl -n distill logs -f job/gh0stx-qlora | grep -avE "Loading weights"

# что смотреть в логе:
#   post-load GPU mem (GB)  -> ~34/GPU на L40S, есть запас
#   adapted matrices        -> LoRA прикрутилась (200-900 матриц)
#   loss                    -> пошло обучение
# итог: адаптер -> s3://ai-monitoring-test-123456789012/gh0stx-rft/adapter
```

Проверка ёмкости без запуска: `aws ec2 get-spot-placement-scores --instance-types
g6e.48xlarge --target-capacity 8 --target-capacity-unit-type units --region-names
eu-north-1 --single-availability-zone`. Квота:
`aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA`.

nvfp4-путь (LoRA поверх готовой NVFP4-базы) отвергнут после проверки на боксе: HF
transformers держит роутинговых экспертов fused-3D `nn.Parameter`, а чекпоинт хранит их
per-expert 2D nvfp4-packed. `run_compressed=True` эту разницу деквантует в dense — 120
«missing»-тензоров экспертов = **262GB**, в 128GB бокса не влезает. 84GB-компрессия живёт
только в MoE-ядре vLLM, обучать её нельзя. Файлы `train_qlora_nvfp4.py`/`train_box_nvfp4.py`
оставлены как запись тупика.

### H200 spot (реальный прогон, 2026-08-26)

Обучение прошло на `p5e.48xlarge` spot (8× H200 141GB = 1128GB) в eu-north-1a, пул
`gpu-distill-h200`, `train/job-qlora-h200.yaml`. Ёмкость поймана так: on-demand p5/p5en в
eu-north-1 отдают `InsufficientInstanceCapacity` по всем AZ, но в пул добавлен
`p5e.48xlarge` (предлагается в 1a) + `spot` — реальный `CreateFleet` попал в спотовый карман
(placement score врал про дефицит; ёмкость проверяется попыткой, не опросом).

eu-north-1 предлагает только семейство **p5**: `describe-instance-type-offerings` возвращает
`p4d.24xlarge, p5.48xlarge, p5e.48xlarge, p5en.48xlarge` (плюс L40S `g6e.48xlarge`). **p6
(B200/GB200) в eu-north-1 отсутствует** — в nodepool ставить `p5*`, не `p6*`. Ёмкость дважды
поймана на `p5e.48xlarge` spot (2026-08-26 и 2026-08-31). p4d.24xlarge (8×A100-40=320GB) не
брать — OOM на 271GB-модели.

Настройки, стоившие рестартов:
- `PER_GPU_MEM=44GiB`, не выше. При 120GiB `infer_auto_device_map` заполняет GPU0 до 136GB,
  и `prepare_model_for_kbit_training` (апкаст норм в fp32) падает с OOM. 44GiB размазывает
  модель на 8 карт по ~34GB, ~107GB на карту под активации.
- База на `emptyDir` (kubelet кладёт на instance-store RAID0, 29TB — 262GB влезает), не на
  hostPath root-тома 300Gi (`ENOSPC` на 13-м шарде).
- `save_strategy=steps`, `save_steps=25` + фоновый `aws s3 sync` каждые 120с в entrypoint —
  спот-отзыв стоит отката к последнему S3-чекпоинту, не всего прогона.
- Темп ~2-3 мин/шаг (naive pipeline MP), 3 эпохи (442 шага) > 12ч дедлайна. Адаптер снимаем
  на 1-2 эпохах из S3-чекпоинтов.
- `train_qlora.py` без resume: любой рестарт пода (отзыв спота) начинает с шага 0 и пересинк
  базы. Спот отзывали дважды; прогон дошёл до **checkpoint-275, epoch 1.861, loss 0.40→0.10**,
  и это финал — гоняться за 3-й эпохой на нестабильном споте смысла нет. Чекпоинт закреплён в
  `s3://…/gh0stx-rft/adapter-final/` (плюс локально `training/adapter-final/`). Job удалить сразу
  после снятия: иначе ретрай на новом споте `s3 sync --delete` затрёт `adapter/`.

## Шаг 6. Merge адаптера в bf16

`train/merge_adapter.py` (тот же meta-move-патч на 5 битых слоёв, что в `train_qlora.py`):
грузит базу на CPU (`device_map=cpu`, mmap), `PeftModel.from_pretrained(base, adapter).
merge_and_unload()`, сохраняет merged bf16 ~282GB (61 шард). merge_and_unload быстрый — LoRA
тронула только attention/shared_expert/linear_attn, 262GB экспертов не трогаются; медленная
часть — запись 282GB. GPU не нужен → **CPU r6i.24xlarge (768GB RAM)**, on-demand в eu-north-1
(ёмкость есть, в отличие от GPU), ~30-60 мин, ~$10. Инфра: `train/entrypoint_merge.sh`,
`train/job-merge-cpu.yaml`, `train/nodeclass-merge-cpu.yaml`. Выход: `s3://…/gh0stx-rft/merged/`.
Merged держит префикс `model.language_model.layers.*` (как база), НЕ `model.layers`.

## Шаг 7. Реквант в NVFP4 (графт)

Ствольного `nvfp4_quant.py` (owner's, из HANDOFF) на боксе НЕТ — `scratchpad/aws/` эфемерный,
стёрся. Написан `quant/graft_requant.py`: квантует в NVFP4 только изменённые LoRA-модули и
графтит на `dflash_only`, оставляя 262GB nvfp4-экспертов и их калибровку нетронутыми.

Что квантуется/копируется (карта из `config.layer_types`, паттерн 3×`linear_attention` +
1×`full_attention`, 60 слоёв → 15 attention-слоёв):
- **квант NVFP4**: `shared_expert.{gate,up,down}_proj` (все слои) + `self_attn.{q,k,v,o}_proj`
  (15 attention-слоёв) = 240 модулей;
- **копия bf16**: `linear_attn.{in_proj_qkv,in_proj_a,in_proj_b,in_proj_z,out_proj}` (45
  linear-слоёв, в ignore-листе) = 225 модулей;
- всё прочее (эксперты, embed, lm_head, нормы, роутер, conv1d) — из `dflash_only`.

Формат (compressed-tensors 0.18, `preset_name_to_scheme("NVFP4")`): `weight_packed` U8
[out, in/2], `weight_scale` F8_E4M3 [out, in/16], `weight_global_scale` F32, group_size 16,
observer `memoryless_minmax` (scale из веса → **калибровка не нужна**). Активации динамические
(`dynamic:local`) → `input_global_scale` НЕ пересчитывается, берётся из `dflash_only` (вход в
слой определяют вышестоящие слои, LoRA поменяла веса, не вход). Ядро квантайзера: `generate_gparam`
+ `calculate_qparams` + `quantize` + `pack_fp4_to_uint8`; round-trip ошибка 9% (норма 4-бит).

Топология (у бокса нет AWS-кредов, merged 282GB, dflash 84GB уже на боксе):
1. бокс → `dflash_meta` (240 `input_global_scale` + config, 32KB) → мак → S3;
2. **AWS m6i.4xlarge** (`MODE=quant`): синк merged из S3 + `dflash_meta` → граф-шард ~18GB →
   `s3://…/gh0stx-rft/graft/`. Инфра `train/entrypoint_quant.sh`, `train/job-quant-cpu.yaml`;
3. мак качает 18GB → scp на бокс;
4. бокс (`MODE=assemble`): хардлинк локальных dflash-шардов + граф-шард + пересбор `index.json`
   → новый релиз (качать 84GB не нужно).

Шаблоны рядом: `quant/nvfp4_quant_mtp.py` (MTP-голова через oneshot), `quant/merge_mtpq_release.py`
(графт-паттерн: правка ignore/index), `quant/build_dflash_only.py`, `quant/quantize_k4_nvfp4.reference.py`.

**Статус (2026-08-27): пайплайн замкнут, обученная модель задеплоена и работает.**
`gh0stx_rft_v2` на боксе (`~/nvfp4_v4_releases/`): raw `/v1/completions` на «The capital of France is»
→ «Paris, and the capital of the United States is Washington, D.C.»; chat «2+2» → «4». 3 шарда
(dflash-ствол 90GB хардлинками + граф-шард 11.9GB, index перепривязан на 1185 ключей).

**Баг реквванта и фикс (важно).** Первый прогон давал мусор (`,,,,,,`). Изоляция тремя деплоями:
pure-copy графт (байт-в-байт тензоры dflash) → связно (структура сборки верна); identity графт
(веса dflash, переквантованные моим `quant_pack`) → мусор (баг в кванте); identity-v2 (тот же квант,
но `weight_global_scale` берётся ИЗ dflash, не пересчитывается) → связно. Причина: `quant_pack`
пересчитывал `weight_global_scale` через `generate_gparam` (16482 вместо dflash 16512), а
`input_global_scale` брался от dflash. W4A4-ядро дэкваантует выход GEMM через связку
weight_global_scale × input_global_scale — рассинхрон ломал вывод в мусор (веса при этом поэлементно
верны, коды 99.9% совпадали, дифф — 2× в 0.1% групп на fp8-floor). Фикс в `quant/graft_requant.py`:
`quant_pack(W, gs)` берёт `gs = dfl.get(...weight_global_scale)`, не пересчитывает. Правило: при
графте на существующий релиз переиспользуй ОБА global scale (weight и input) из целевого релиза.

Не надо гнать AWS-квант заново после фикса: багованный граф-шард в S3/на боксе дескваантуется
самосогласованно (восстанавливает merged-вес независимо от кривого global_scale), поэтому
`fix_graft_shard.py` на боксе переквантует его с точным global_scale dflash — без пересинка 262GB.

Деплой сейчас БЕЗ DFlash (`deploy_plain_v1.sh`, без `--speculative-config`): дрэфтер
`qwen35-397b-dflash` матчен под исходный dflash_only, на изменённой main-модели acceptance 0% →
скорость ~5-8 t/s вместо ~20. Restore DFlash — re-match дрэфтера (отдельная задача скорости).
Откат на исходный: `cp ~/dflash_deploy.sh.bak-v1 ~/dflash_deploy.sh && make dflash`.

## Шаг 8. Деплой на gx10

`deploy/dflash_deploy.sh` (= `~/dflash_deploy.sh` на боксе): `TARGET` → новый релиз в
`nvfp4_v4_releases/`, контейнер `vllm-dflash`, образ `aeon-vllm:v024`, CTX 90000, mamba
fp16. Запуск `make dflash` (таргет в `deploy/Makefile.box`). Первый запрос холодный (~1 t/s
warmup), тёплый ~19-23 t/s.

## Шаг 9. Проверка

Прогнать `harness/run_authored.sh` через грейдер до и после, сравнить PASS-rate по векторам.
Отдельно проверить впечатанный OOB-вектор (Logstash ruby log-injection из `gh0stx-rules.md`)
и новые семейства из `microservices_security_dataset.json`.

## Что не в git

Живое состояние: `authored_tasks.json` и `sft.jsonl` на боксе растут при каждом харвесте;
здесь снапшот. Адаптер появляется в S3 после первого успешного прогона.

## Грабли

- Харвест параллелит плохо: vLLM с DFlash держит `--max-num-seqs 4`, а несколько воркеров дают
  5+ конкурентных `opencode run` → контеншен → задачи в FAIL. Одиночный последовательный
  супервизор собирает надёжно (так собралось 2017 сырых строк). `OUT` в `run_authored.sh`
  переопределяем через env (бэкап `.bak-outenv`), но общий `sft.jsonl` из двух процессов бьётся
  (JSON-строка >4KB, append не атомарен) — разводить по файлам.
- `emit_from_json.py`: принимает и `{"tasks":[...]}`, и список; `subprocess` с `cwd=W` (иначе
  cwd-относительные задачи выполняются не в W, в отличие от харвестера).
- Live-server задачи (мок-сервер из `setup_sh`): харнесс перед грейдом бьёт `pkill -f "$mark"`,
  но `python3 server.py` маркера в cmdline не несёт → сервер доживает до грейда (это и нужно),
  grader гасит его через `server.pid`. Осиротеет только если задача таймаутит до грейда.
- SSO-токен живёт ~сутки: `aws sso login --sso-session new-sso` (браузер на маке).
- Временные merge/quant-узлы: после job удалять job + nodepool + nodeclass — Completed-под держит
  узел, `WhenEmpty`-консолидация медленная. Karpenter `Never expireAfter` сам не гасит.
- Монитор пода: одно пустое чтение `kubectl get pods` ≠ смерть пода. Гейт по 3 страйкам ИЛИ по
  `job.status.succeeded/failed`, иначе ложное «POD GONE».
- On-demand r/m-family в eu-north-1 встаёт за ~40с (в отличие от GPU-дефицита) — merge/quant туда.

## DFlash-дрэфтер под gh0stx_rft_v2

DFlash-дрэфтер под RFT-модель обучается **fine-tune официального z-lab дрэфтера**, не с нуля.
Деплой `~/dflash_deploy_ft.sh` (DRAFTER `~/models/qwen35-397b-dflash-ft/final`) даёт 22.04 t/s
агрегат на 5 unseen промптах (код/рассуждение/безопасность/sysadmin/алгоритмы), accept length
3.1–5.4, position-0 0.75–0.94. Плюс `--restart unless-stopped`.

Обучение с нуля на доступном self-distill упирается в объём. v3 (корпус свернулся в 63 уникальных
последовательности — greedy на обезличенных шаблонах) переобучился: seen accept 7.29, unseen 1.6.
v4 (2671 уникальных разнообразных, 3 эпохи) недообучился: train-acc встал на 0.30, seen 1.40,
unseen 1.2. SpecForge train-acc не предсказывает serve accept — официальный дрэфтер на этих же
данных показывает train-acc 0.32 при serve 4.85. Официальный обучен на большом opc и генерализует,
архитектурно идентичен (target_layer_ids [1,9,17,25,33,41,49,57], head_dim 128, KV 8); warm-start
от него + 1 эпоха lr 1e-4 на 2671 self-distill rft_v2 адаптирует к распределению rft_v2, сохраняя
генерализацию.

Рецепт: `regen_diverse.py` строит 2671 УНИКАЛЬНЫЙ тематический промпт из тем оригинала (не сами
шаблоны) → gh0stx_rft_v2 генерит ответы (temp 0, кап 448) → `dflash_selfdistill2.jsonl`
(проверка: unique prompts == unique completions == rows). `run_extract_v4.sh` снимает 8 слоёв
(hidden [seq, 32768]) → `dflash_hs_v4`. Warm-start чекпоинт `epoch_0_step_0/` = официальный
`model.safetensors` + `dflash_official_train.json` как config.json (top-level block_size обязателен)
+ `dflash.py`. `run_train_ft1.sh`: `--resume --num-epochs 1 --learning-rate 1e-4 --save-interval
999999`. Сборка `final/` из `epoch_1_step_2671` → deploy.

Грабли: serve + training одновременно переполняют 121GB unified memory GB10 → своп-трэшинг →
sshd висит, нужен ребут (training-скрипты глушат serve первым). Kill контейнера во время
save-interval save обрезает `model.safetensors` (220MB вместо 2.58GB) — финальный save после цикла
эпох чистый, не прерывать. `--resume` берёт global_step из имени чекпоинта — для старта эпох с нуля
имя `epoch_0_step_0`. Промежуточные чекпоинты без `dflash.py` (serve через vLLM его не требует).
Официальный дрэфтер на rft_v2 без дообучения — fallback (accept 4.85, `~/dflash_deploy_official_on_rft.sh`).

## Data prep docs

- `COMMIT-FORMAT.md` — the SFT commit-format contract (why targets close `</think>` and commit).
- `DATA-GATHERING.md` — instruction for gathering offsec SFT data locally in commit-format: task shape, harvest loop, coverage by `OFFSEC-GAPS.md`, validation gate.
