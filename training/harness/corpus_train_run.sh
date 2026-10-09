#!/usr/bin/env bash
# One-shot corpus train + held-out delta, with guaranteed service restore (trap).
# serve-down -> train_141b_requant on corpus -> serve-eval epoch_1 -> held-out -> restore.
set -u
BASE=/home/gh0stx/nvfp4_v4_releases/gh0stx_rft_v5
OUT=/home/gh0stx/gb10/training/adapters/egt_corpus
DATA=/tmp/corpus_sft.jsonl
EVAL_CT=vllm-egt-eval
IMG=aeon-vllm:v024

restore() {
  echo "=== restore $(date +%H:%M:%S) ==="
  sudo -n docker rm -f "$EVAL_CT" >/dev/null 2>&1
  sudo -n docker start vllm-dflash
  sudo -n docker start open-webui
  ( cd ~ && setsid nohup python3 ~/proxy.py >/tmp/proxy_restart.log 2>&1 < /dev/null & )
  echo "restore issued"
}
trap restore EXIT

echo "=== serve-down $(date +%H:%M:%S) ==="
kill $(pgrep -f "python3 /home/gh0stx/proxy.py") 2>/dev/null || true
sudo -n docker stop open-webui
sudo -n docker stop vllm-dflash
sleep 6
free -g | grep Mem

echo "=== train EPOCHS=2 GA=1 $(date +%H:%M:%S) ==="
rm -rf "$OUT"; mkdir -p "$OUT"
DATA="$DATA" OUT="$OUT" EPOCHS=2 GA=1 LR=2e-4 MAXLEN=8192 CHUNK=2048 SAVE_STEPS=50 \
  /home/gh0stx/.venvs/train/bin/python /home/gh0stx/train_141b_requant.py \
  || { echo "TRAIN FAILED"; exit 1; }

echo "=== serve-eval epoch_1 $(date +%H:%M:%S) ==="
sudo -n docker rm -f "$EVAL_CT" >/dev/null 2>&1
sudo -n docker run -d --name "$EVAL_CT" --gpus all --ipc=host -p 8000:8000 \
  -e TORCH_CUDA_ARCH_LIST=12.1a -e VLLM_USE_FLASHINFER_SAMPLER=1 \
  -v "$BASE":/model:ro -v "$OUT/epoch_1":/adapter:ro \
  "$IMG" -lc "vllm serve /model --served-model-name gh0stx --quantization compressed-tensors --gpu-memory-utilization 0.82 --load-format runai_streamer --enforce-eager --max-model-len 16384 --max-num-seqs 2 --trust-remote-code --language-model-only --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 --mamba-cache-dtype float16 --enable-lora --max-loras 1 --max-lora-rank 16 --lora-modules gh0stx-r2=/adapter --host 0.0.0.0 --port 8000"
echo "waiting eval serve..."
for i in $(seq 1 60); do curl -s --max-time 4 http://localhost:8000/v1/models 2>/dev/null | grep -q gh0stx-r2 && { echo "eval ready $(date +%H:%M:%S)"; break; }; sleep 10; done

echo "=== held-out AFTER $(date +%H:%M:%S) ==="
MODEL=gh0stx-r2 /home/gh0stx/gb10/training/harness/heldout_eval.sh

echo "=== done $(date +%H:%M:%S) ==="
