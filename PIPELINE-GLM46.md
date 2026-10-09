# PIPELINE-GLM46 — аблитерация GLM-4.6 под GB10

Порядок как в [PIPELINE.md](PIPELINE.md), для базы `zai-org/GLM-4.6` (glm4_moe). Прун НУЖЕН:
полная GLM-4.6 (353B) в NVFP4 это ~198ГБ (4 бита = 0.56 Б/парам), в 122ГБ GB10 не влезает —
оценка «~106ГБ» из первой сессии ошибочна. Режем 353B → ~151B (REAP, 64/160 эксп/блок), тогда
NVFP4 ~85ГБ + fp8 KV на 120k (~23ГБ) + оверхед влезают. Стадии: ADJUST (аблитерация) →
PRUNE (REAP glm4_moe) → QUANT (NVFP4) → DEPLOY (vLLM на gx10, `--kv-cache-dtype fp8 --max-model-len 120000`).
Порядок иной, чем у Qwen (там prune→adjust): абилитировали всю модель, режем уже абилитированную,
сохранённые эксперты остаются абилитированными. Язык ответов — русский, профиль offensive-security.

Проба стока подтвердила смысл затеи: сильный offsec, 0/5 жёстких отказов на своей-цели, только
safety-оговорки, которые снимает аблитерация. Детали — memory `glm46-abliteration-candidate`.

## Арх GLM-4.6 (Glm4MoeForCausalLM / glm4_moe)

- 355B всего, ~32B активных, 92 слоя, hidden 5120, 160 экспертов top-8, 1 shared, top_k gate.
- `first_k_dense_replace=3`: слои 0..2 — плотный `mlp` (gate/up/down), слои 3..91 — MoE (89).
- Эксперты в HF-дереве — ModuleList `mlp.experts[e].{gate,up,down}_proj` (не packed, как у Qwen).
- shared — `mlp.shared_experts` (множественное), роутер — `mlp.gate`.
- bf16 на диске ~705ГБ (92 шарда) плюс отдельный `mtp.safetensors` (~8ГБ, nextn/MTP-голова).
- MTP-веса НЕ в `model.safetensors.index.json`, HF-загрузка их игнорирует, `num_layers=92`
  (проверено логом). Аблитерация MTP не трогает; в выход `glm46-abliterated` MTP не попадает.
  Для спекулятивного MTP при serve голову берут из стоковой `glm46-base/mtp.safetensors`.

## Порт скриптов под glm4_moe

`pipeline/merge_abliterate.py` — `abliterate()` определяет layout сам: Qwen (packed эксперты
`[E,H,I]`, GDN `linear_attn.out_proj`, singular `shared_expert`) против GLM (per-expert ModuleList
`down_proj`, dense `mlp.down_proj` на слоях 0..2 через `ABL_DO_DENSE_DOWN`, plural `shared_experts`).
o_proj берётся на всех слоях с guard `w.shape[0]==H`. GDN-ветка на GLM — no-op.

`pipeline/nvfp4_quant.py` — `DEFAULT_IGNORE` арх-агностичен: `mlp.gate$` ловит роутер (не
gate_proj), `norm.*` — все нормы, `linear_attn.*`/`shared_expert_gate$` — Qwen-only no-op на GLM,
`eh_proj`/`mtp` держат MTP в bf16. Per-expert и shared_experts FFN квантуются (это нужно).

Deploy без rewrap: llm-compressor пишет `architectures=[Glm4MoeForCausalLM]`/`model_type=glm4_moe`,
vLLM грузит нативно. Хак `fix_config_np.py` из Qwen-ветки (там vLLM корёжил fused-имена GDN) не нужен.

## Ноды и S3

Размер ноды меряется по стадии, не по 4-бит размеру. bf16-загрузка держит ~705ГБ в RAM.

| стадия | нода | почему |
|---|---|---|
| ADJUST | r7i.48xlarge spot $1.64 (1536ГБ, CPU) | дешёвая с нужной RAM; nodeclass glm-abliterate, корень 1500Gi |
| QUANT | p4d.24xlarge spot $4.54 (1152ГБ, 8×A100-40) | дешевле g6e.24xlarge $4.67 и с запасом RAM; nodeclass glm-quant, корень 1000Gi |
| DEPLOY | GB10 gx10 (122ГБ unified, sm_121a) | локально, aeon-vllm; serve FP4 GEMM только тут |

S3 `s3://ai-monitoring-test-123456789012/`: `glm46-base/` (сток+mtp), `glm46/` (скрипты+256 пар),
`glm46-abliterated/` (выход ADJUST), `glm46-nvfp4/` (выход QUANT, флаг `_NVFP4_COMPLETE`).

Ноды одноразовые: taint + do-not-disrupt держат ноду на прогоне, ожидатель сам `kubectl delete job`,
Karpenter консолидирует пустую (memory `karpenter-idle-after-job`). Диск ноды правится через
EC2NodeClass blockDeviceMappings.

## Аблитерация: параметры

256 пар harmful/harmless (кап с 1547, замер направления на CPU иначе долгий), coeff 1.5,
norm-preserving (обязателен под NVFP4), layer `int(0.6*92)=55`. Флаги `ABL_*` все on, `--skip-lora`.
Таргеты по факту прогона: o_proj 92, per-expert down_proj 89 слоёв (14240 срезов), dense down_proj
3 слоя, shared_experts 89, embed.

## Два бага (пойманы на прогонах 2026-09-27)

`FileNotFoundError: mtp.safetensors` — синк с `--exclude mtp.safetensors` убрал файл, но
`from_pretrained` открывает каждый шард из индекса и падает на отсутствующем. Синкаю полный набор.

ENOSPC при сохранении на ноде 1300Gi — `from_pretrained` mmap-ит входные safetensors, `rm` мид-прогоне
отвязывает имена, но блоки держатся, пока процесс держит отображение (`df` 692G при `du` 24M,
пусто в `/proc/PID/fd`). Трюк с удалением не освобождает mmap-блоки. Фикс — нода с корнем 1500Gi,
вход и выход (по ~705ГБ) лежат рядом, удаление убрано.

## Deploy на gx10 (staged, требует подтверждения)

gh0stx serve держит ~93% из 122ГБ (`--gpu-memory-utilization 0.93`, :8000, DFlash+LoRA gh0stx-r2,
в контейнере aeon-vllm). GLM-4.6 NVFP4 (~106ГБ) рядом не встанет, deploy = сначала остановить gh0stx.
Действие ломает дейли-драйвер, поэтому автоматически не выполняется. Флаги serve (reasoning-parser,
tool-call-parser glm45) сверяются против aeon-образа на этапе deploy.

## Не проверено

Размер `glm46-abliterated` и `glm46-nvfp4` на диске — TBD после прогонов. Держит ли llm-compressor
glm4_moe MoE-калибровку без правок — проверяет dry-прогон на 4 сэмплах перед полным. NVFP4 glm4_moe
на sm_121a при serve — не проверено, это гейт стадии DEPLOY. Скорость decode на GB10 — TBD.
