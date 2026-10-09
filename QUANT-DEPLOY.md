# QUANT-DEPLOY — NVFP4 квантизация (шаг [3]) + vLLM на GB10 (шаг [4])

> Статус: ⬜ НЕ ЗАПУЩЕНО (блокер: GPU). Рецепт валидирован на бумаге против официального
> llm-compressor Qwen3.5 NVFP4-MoE примера + внешних прецедентов; артефакта пока нет.
> Скрипт: `pipeline/nvfp4_quant.py`. Манифест: `pods/nvfp4-pod.yaml`.
> (Слияние старых DEPLOY-NOTES.md + DOWNSTREAM.md.)

---

# ШАГ [3] — NVFP4 (W4A4)

## 3.1 Что делаем

`decensored_v2` (BF16) → `nvfp4_final` (compressed-tensors, 4-бит) через llm-compressor `oneshot`.
Вход ДОЛЖЕН быть norm-preserving-abliterated (`decensored_v2`), НЕ плоско-abliterated — иначе
row-norm outlier'ы дадут коллапс под FP4 (скрипт это не проверяет).

## 3.2 Ignore-list (что оставить в BF16)

**Квантуем:** routed `experts.*` + `shared_expert.{gate,up,down}_proj` + full-attn `self_attn.{q,k,v,o}_proj`.
**Оставляем BF16 (ignore):**
```
re:.*lm_head, re:.*norm.*, re:.*embed_tokens$, re:.*mlp\.gate$, re:.*shared_expert_gate$, re:.*linear_attn.*
```

- `linear_attn.*` (GatedDeltaNet) ОБЯЗАН остаться BF16: vLLM ФЬЮЗИТ GDN-проекции
  (in_proj_qkv/z/a/b → единый in_proj_qkvz) + causal conv1d; квантизация любого `linear_attn.*` ломает
  fused-кернел.
- `re:.*mlp\.gate$` — экранированная точка + `$`, чтобы НЕ поймать `shared_expert_gate`/`gate_up_proj`.
  Не упрощать.
- ⚠ Раннее наблюдение (что убило nvfp4_v2): попытка держать full-attn/shared в BF16 (rene98c-style)
  заставляет vLLM корёжить fused-имена (`qkqkv_proj`, `gate_gate_up_proj`) → 160+ весов
  битые/пропущены. Поэтому наш дефолт = КВАНТОВАТЬ full-attn + shared (как в `nvfp4_quant.py`).

## 3.3 Как запускать

GPU-узел (p4d 8×A100-40 достаточно: BF16 CPU-resident, oneshot онлоадит по одному блоку на карту):
```bash
NVFP4_IN=/data/decensored_v2 NVFP4_OUT=/data/nvfp4_final \
  NVFP4_SAMPLES=512 NVFP4_MAXSEQ=2048 python3 nvfp4_quant.py
```
- `device_map=None` (CPU-resident) — при `auto` MoE-линеаризация оффлоадит на уже-полный GPU0 → CUDA OOM.
- Калибровка — англ. ultrachat_200k (для activation-scale, не для выбора весов); НЕ путать с
  domain-tagged корпусом prune. NVIDIA-референс: cnn_dailymail + Nemotron-Post-Training-v2, ~512 samples.
- Стек: `transformers==5.12.1`, `llmcompressor>=0.9.0`, compressed-tensors, `trust_remote_code=True`.

## 3.4 Где взять GPU (актуальный блокер)

- eu-north-1: H100 (p5) → InsufficientCapacity (on-demand И spot, все AZ); H200 не появляется (ждали
  сутками); p4d (8×A100-40=320 ГБ) — для КВАНТИЗАЦИИ хватает (по-блочный онлоад).
- **Вариант A:** GPU-нодпул в example-staging eu-west-1 (тот же аккаунт 123456789012, другой пул capacity).
- **Вариант B:** стриминговая квантизация на самом GB10 (Blackwell). Техника streaming-apply
  (jim-plus/llm-abliteration): правки по одному шарду → полная 297 ГБ BF16 не резидентна.
- S3 читается кросс-регионально (~$6 egress на 297 ГБ). Диск: 297 ГБ BF16 + ~85 ГБ NVFP4 — стейджить
  через S3 / писать по-шардово.

## 3.5 После квантизации

`test_uncensor_check.py` на 4-бит весах — послушность + навыки должны пережить FP4. Байт-точные пробы
(offsets/GUID/hex) гонять ПОСЛЕ квантизации (4-бит шум бьёт по низкомаржинальной символьной точности —
иначе спишем на prune).

---

# ШАГ [4] — vLLM serve на GB10

## 4.1 Rewrap config (обязательно перед serve)

llm-compressor сохраняет ПЛОСКИЙ конфиг (`Qwen3_5MoeForCausalLM` / `qwen3_5_moe_text`) → vLLM уводит
в мультимодальный VL-путь → краш. Обернуть:
- `architectures=[Qwen3_5MoeForConditionalGeneration]`, `model_type=qwen3_5_moe`
- `text_config` = весь плоский конфиг (`model_type qwen3_5_moe_text`, без `architectures`)
- `quantization_config` наверх, БЕЗ `vision_config`
- Веса уже `model.language_model.*` — совпадают.
- ⚠ При деплое дописать `re:.*linear_attn.*` в `config.json → quantization_config.ignore`:
  llm-compressor пишет ignore как `model.layers.N.*`, а vLLM-модули — `model.language_model.layers.N.*`
  → префикс-mismatch → vLLM переквантует GDN → «not found». Регэксп префикс-агностичен → чинит.
  (Скрипт rewrap: `fix_config_np.py` / `fix_config_v2.py` в `~/distill/` — делает rewrap + linear_attn regex.)

## 4.2 Команда serve (boot-tested, gx10 aeon-образ)

```bash
vllm serve /model --quantization compressed-tensors \
  --kv-cache-dtype fp8_e4m3 --gpu-memory-utilization 0.85 \
  --max-model-len 32768 --max-num-seqs 4 \
  --enable-chunked-prefill --enable-prefix-caching --trust-remote-code \
  --language-model-only --load-format runai_streamer --enforce-eager \
  --max-num-batched-tokens 8192 --host 0.0.0.0 --port 8000
```

**Обоснование флагов:**
- `--language-model-only` — сервить только LM обёртки (пропустить vision-tower+processor).
- `--max-num-batched-tokens 8192` — ОБЯЗАТЕЛЬНО: Mamba/GDN cache align требует block_size(4176) ≤
  max_num_batched_tokens (дефолт 2048 → AssertionError).
- `--load-format runai_streamer` — стримит веса, избегает ~50 ГБ host-shard буфера (модель ~88–97 ГБ
  на 122 ГБ unified — впритык).
- `--enforce-eager` — без cudagraph-спайка памяти.
- Anti-loop на request-time: `chat_template_kwargs:{enable_thinking:false}`.

## 4.3 Образ / арх

- GB10/gx10 = ARM64 Grace+Blackwell, 122 ГБ unified, sm_121a. Образ `ghcr.io/aeon-7/aeon-vllm-ultimate`
  (vLLM 0.22.1+pr44389.aeon) — **ARM64, только GB10**; на x86 (p4d) → exec format error.
  Облачные x86-задачи (квантизация) — `vllm/vllm-openai:latest`.
- sm_121 FP4 GEMM fix = vLLM PR #40082 (2026-05-20). MoE FP4 backend: native `cutlass_moe_fp4`
  (не ставить `VLLM_USE_FLASHINFER_MOE_FP4`) ИЛИ `--moe-backend=flashinfer_cutlass`. Сверить образ
  с флагами перед деплоем: кандидаты `vllm/vllm-openai:cu130-nightly` / aeon-образ (vLLM 0.23, 7 патчей sm_121a).

## 4.4 KV / speculative decoding

- **KV = BF16, НЕ fp8:** fp8_e4m3 KV несовместим с DFlash на GB10/sm_121a, а DFlash хотим.
  BF16 KV ~4.6 ГБ@150k, ~6.1 ГБ@200k. (См. memory `gx10-fp8-kv-dflash-incompatible`.) Для 200k-контекста
  fp8 KV + max-num-seqs 2–4 нужен только в комбо с MTP.
- **Speculative:** z-lab DFlash-драфтер мисматчит pruned+abliterated таргет (кондишенится на hidden
  states → просадка acceptance; рецепт тренировки не выпущен → matched drafter не переобучить).
  Fallback = нативный Qwen3.5 MTP-head (`num_speculative_tokens=1`, ~83–93% accept, иммунен к prune).
  DFlash и fp8-KV взаимоисключающи.

## 4.5 Внешние прецеденты (фича-фит для GB10 NVFP4)

- bjk110/SPARK_Qwen3.5-122B-A10B-NVFP4 — 122B гибрид GDN+full-attn all-MoE NVFP4 на ОДНОЙ GB10, ~26–31 t/s.
- AEON-7 heretic-NVFP4-DFlash — пруф abliterate→NVFP4→serve на GB10.
- NVIDIA nvidia/Qwen3.5-397B-A17B-NVFP4 (modelopt 0.42) — подтверждает, что базовая арх квантуется.

## 4.6 Арх-факты (для справки)

Qwen3.5 гибрид: 15 full-attn + 45 GatedDeltaNet linear-attn, 60 блоков. Упакованные 3D-эксперты
(`Qwen3_5MoeExperts.gate_up_proj/down_proj` — `nn.Parameter`, НЕ `nn.Linear`). H=4096, vocab=248320,
num_experts=184 (post-prune), top_k=10. NVFP4 W4A4 Blackwell/sm_121a.
Скорость декода задаётся top_k=10 активных экспертов + attention + DFlash, НЕ KEEP_FRAC (prune
освобождает RAM, но не ускоряет декод — те же 10 экспертов/токен). KEEP_FRAC-рычаги по RAM весов:
0.32≈77 ГБ / 0.36≈85 ГБ / 0.40≈94 ГБ / 0.46≈107 ГБ (впритык).
