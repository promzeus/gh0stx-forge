# AEON Qwen3.8-27B + DFlash2 serving (GB10)

Candidate local base (uncensored, dense-27B, locally QLoRA-trainable). Off-the-shelf DFlash2
drafter exists — no need to train our own.

## Models
- Target: `AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16` (uncensored) or `Qwen/Qwen3.8-27B`
  (clean base). BF16 ~55.6GB, NVFP4 variant smaller for serving.
- Drafter: `incoai/Qwen3.8-27B-DFlash2` (2B, block-diffusion DFlash2 — newer/faster than the
  original DFlash gh0stx uses). Mirror: `z-lab/Qwen3.8-27B-DFlash2`.
- Container (GB10, DFlash-patched vLLM): `ghcr.io/aeon-7/aeon-vllm-ultimate:latest`.

## Launch (vLLM)
```
vllm serve <target> \
  --speculative-config '{"method":"dflash","model":"incoai/Qwen3.8-27B-DFlash2","num_speculative_tokens":7}' \
  --mamba-cache-dtype float32 \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder --enable-auto-tool-choice \
  --max-num-seqs 16 --max-num-batched-tokens 16384 --gpu-memory-utilization 0.70 \
  --enable-chunked-prefill --enable-prefix-caching --trust-remote-code
```
`num_speculative_tokens`: 3.8 card says 7; AEON's 3.6 deploy uses 10 — tune by acceptance.
For NVFP4 target add `--quantization modelopt`.

Env (GB10 / sm_121a): `TORCH_CUDA_ARCH_LIST=12.1a VLLM_USE_FLASHINFER_SAMPLER=1
VLLM_NVFP4_GEMM_BACKEND=flashinfer-cutlass PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.

## Sampling — for correct answers
Reasoning: **temperature 1.0, top_p 0.95, top_k 20, reasoning effort xhigh** (Qwen3.8 card).
Agentic/tool: `enable_thinking:false`, lower temperature.

NOTE: greedy (temp 0), which our earlier gh0stx/audit tests used, is wrong for these reasoning
models — it drives degenerate loops/repetition. Part of the "commit non-termination" we
diagnosed may be mis-sampling, not only the tune. Always run these at the recommended sampling.

## Refs
- https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2
- https://github.com/AEON-7/vllm-dflash
- https://github.com/AEON-7/Qwen3.6-27B-AEON-Ultimate-Uncensored-DFlash
- https://github.com/z-lab/dflash
