#!/bin/bash
# dflash_deploy.sh — live DFlash deployment for gh0stx on gx10 (GB10).
# 2026-08-16: 90k ctx @ ~19.4 t/s (100k fits too, but 90k leaves cuBLAS workspace headroom:
#   at 100k a tool-call request once died with CUBLAS_STATUS_INTERNAL_ERROR on a memory peak). Two changes vs the 65k/18.5 config:
#   * dflash_only release   — trunk without the MTP head (unused under DFlash, but vLLM loaded it):
#                             KV 10.13 -> 11.03 GiB, speed 18.5 -> 22.9 t/s at 65k.
#   * mamba fp16 + batched 8192 — KV 11.03 -> 14.29 GiB (133,649 tokens), unlocks 100k ctx.
#     Verified: 62k-token needle prompt answered correctly in 44.2 s.
# Image is PINNED: ghcr :latest moved to 0.26.0, which changes memory behaviour on this 90GB model.
# Loading 90GB weights normally spikes swap to ~20GB for 1-2 min — do not kill it, box has 51GB swap.
# Rollback (65k, faster, mamba fp32): set CTX=65536, MAMBA=float32, BATCHED=16384.
# Fallback with MTP head + 131k: bash ~/deploy_mtp_overlay.sh activate calibrated_mtpq_v1
set -euo pipefail
CONTAINER=vllm-dflash
IMG=aeon-vllm:v024
TARGET=${GH0STX_RELEASE:-/home/gh0stx/nvfp4_v4_releases/gh0stx_rft_v4}
DRAFTER=${GH0STX_DRAFTER:-/home/gh0stx/models/qwen35-397b-dflash-ft/final}
CTX=90000
MAMBA=float16
BATCHED=8192

sudo docker rm -f "$CONTAINER" 2>/dev/null || true
# CUDA context must be released before a new run, else inductor dies with
# "CUDA driver error: operation not permitted".
waited=0
while [ $waited -lt 60 ]; do
  pgrep -af 'EngineCore|vllm serve' 2>/dev/null | grep -v 'pgrep -af' | grep -q . || break
  sleep 5; waited=$((waited + 5))
done
sleep 20

sudo docker run -d --name "$CONTAINER" --gpus all --ipc=host -p 8000:8000 \
  --restart unless-stopped \
  -v "$TARGET":/model:ro -v "$DRAFTER":/drafter:ro \
  "$IMG" -lc \
  "vllm serve /model --served-model-name gh0stx --quantization compressed-tensors \
   --gpu-memory-utilization 0.88 --max-model-len $CTX --max-num-seqs 4 \
   --enable-chunked-prefill --enable-prefix-caching --trust-remote-code \
   --language-model-only --load-format runai_streamer --max-num-batched-tokens $BATCHED \
   --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
   --mamba-cache-dtype $MAMBA \
   --speculative-config '{\"method\":\"dflash\",\"model\":\"/drafter\",\"num_speculative_tokens\":10}' \
   --host 0.0.0.0 --port 8000"
echo "vllm-dflash запущен: ${CTX} ctx, mamba ${MAMBA}, batched ${BATCHED}, образ ${IMG}"
