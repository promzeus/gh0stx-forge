---
license: apache-2.0
base_model: Qwen/Qwen3.5-397B-A17B
pipeline_tag: text-generation
language:
  - en
  - ru
  - zh
tags:
  - qwen3_5_moe
  - moe
  - nvfp4
  - compressed-tensors
  - speculative-decoding
  - dflash
  - mtp
  - dgx-spark
  - gb10
---

# gh0stx-nvfp4

Expert-pruned, **NVFP4-quantized** derivative of Qwen3.5-397B-A17B that runs on a **single NVIDIA GB10 (DGX Spark, 128 GB unified)** with speculative decoding. Two accel paths are supported: **DFlash** (fastest, recommended) and a built-in **MTP head** (fallback, no external drafter).

## Specs
- **Base:** Qwen3.5-397B-A17B (hybrid GatedDeltaNet + sparse MoE + periodic full-attention)
- **Prune:** expert-pruned 512 → **174 experts** with a **domain-calibrated keep-set** (routing-frequency over a balanced corpus, not raw router-norm) → **~141B total / ~17B active**
- **Quant:** NVFP4 W4A4 (compressed-tensors), ~90 GB weights
- **Context:** up to 131K (MTP path); 65K (DFlash path — see tradeoff below)
- **Hardware:** NVIDIA **Blackwell only** (`sm_120/sm_121a`) — NVFP4 is Blackwell-native and will **not** run on Hopper/Ampere. Validated on a single GB10 (DGX Spark class, 128 GB unified).

## Speed (single-stream decode, one GB10)

| Path | tok/s | mean accept-len | Context | Notes |
|---|---|---|---|---|
| **DFlash (recommended)** | **~18.5** | ~4.4 tok/step | 65K | external z-lab drafter; needs `--mamba-cache-dtype float32` |
| MTP head (built-in) | ~13 | ~2.0 tok/step | 131K | no external drafter; `qwen3_5_mtp` |
| no spec-decode | ~6 | — | 131K | baseline |

Single-stream decode on GB10 is **latency-bound** (unified LPDDR5X ≈ 273 GB/s; the SM clock sits low and power is far under budget) — so the win comes from accepting more tokens per pass, not from clocks. DFlash drafts ~4.4 accepted tok/step vs MTP's ~2.0. Measured 5-run avg 18.34–18.56 tok/s (single-stream, 200 tok @ temp 0, one GB10). Note: DFlash acceptance can decay over very long sessions — restart the server to restore throughput.

## Serve — DFlash (recommended, ~18.5 tok/s)

Requires the [`z-lab/Qwen3.5-397B-A17B-DFlash`](https://huggingface.co/z-lab/Qwen3.5-397B-A17B-DFlash) drafter (auto-downloads) and a GB10-ready vLLM build ([`aeon-vllm-ultimate`](https://github.com/AEON-7) — vLLM 0.24.x for `sm_121a`, carries the DFlash SWA/prefix-cache ports).

```bash
vllm serve promzeus/gh0stx-nvfp4 \
  --served-model-name gh0stx --quantization compressed-tensors --trust-remote-code \
  --language-model-only --load-format runai_streamer \
  --max-model-len 65536 --max-num-seqs 4 --max-num-batched-tokens 16384 \
  --gpu-memory-utilization 0.88 \
  --enable-chunked-prefill --enable-prefix-caching \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --mamba-cache-dtype float32 \
  --speculative-config '{"method":"dflash","model":"z-lab/Qwen3.5-397B-A17B-DFlash","num_speculative_tokens":10}'
```

**The key flag is `--mamba-cache-dtype float32`.** In fp16 the Mamba (GatedDeltaNet) KV page size doesn't reconcile with the DFlash drafter's sliding-window page size, and engine init fails in `unify_kv_cache_spec_page_size` (`assert new_spec.page_size_bytes == max_page_size`). fp32 changes the mamba page size so mamba + full-attn + drafter all unify. Also: **n=10** (n≥12 is crash-correlated on this stack) and **`--max-num-batched-tokens 16384`** are required for DFlash.

**Context tradeoff:** with mamba-fp32 + the drafter, the full 131K KV doesn't fit in 128 GB, so DFlash runs at **65K** (`--max-num-seqs 4`, `--gpu-memory-utilization 0.88`). KV stays **BF16** — fp8 KV is unsupported with the non-causal DFlash drafter, and TurboQuant is guarded off on hybrid GDN+Mamba. For full 131K context use the MTP path below.

## Serve — MTP head (built-in, ~13 tok/s, 131K)

No external drafter; uses the grafted MTP head shipped in this repo.

```bash
vllm serve promzeus/gh0stx-nvfp4 \
  --served-model-name gh0stx --quantization compressed-tensors --trust-remote-code \
  --language-model-only --load-format runai_streamer \
  --max-model-len 131072 --max-num-seqs 2 --max-num-batched-tokens 8192 \
  --gpu-memory-utilization 0.87 \
  --enable-chunked-prefill --enable-prefix-caching \
  --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":3}'
```

## Notes
- **KV cache: keep BF16.** Do not set `--kv-cache-dtype` fp8 — unstable with the linear-attention + spec-decode path (and mandatory BF16 for the DFlash drafter).
- **Thinking mode is ON by default** (Qwen3.5). Disable per request with `chat_template_kwargs: {"enable_thinking": false}` — recommended for agentic/tool use (thinking isn't spec-accelerated).
- **`--gpu-memory-utilization`**: 0.85 solo, up to 0.88 for DFlash headroom; **never exceed 0.88 on GB10** (unified memory thrashes → slowdowns/hangs, not a clean OOM).
- The **MTP head is BF16** (excluded from NVFP4 via `ignore` in `config.json`).

## Credits
- [Qwen](https://huggingface.co/Qwen) for Qwen3.5-397B-A17B.
- [z-lab](https://huggingface.co/z-lab) for [DFlash](https://github.com/z-lab/dflash) and the `Qwen3.5-397B-A17B-DFlash` drafter.
- [AEON-7](https://github.com/AEON-7) for the `aeon-vllm-ultimate` GB10/`sm_121a` build and the DFlash-on-DGX-Spark recipe (the `--mamba-cache-dtype float32` unlock).

## Sibling
- **[`promzeus/gh0stx-bf16`](https://huggingface.co/promzeus/gh0stx-bf16)** — full BF16 weights (~141B) for other stacks / your own quantization.
