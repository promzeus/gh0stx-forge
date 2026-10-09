#!/usr/bin/env bash
# gh0stx QLoRA on the NVFP4 compressed base. Runs on a p4d (8xA100-40) in 1c.
set -euo pipefail
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
BASE_DIR=${BASE_DIR:-/base}
DATA=${DATA:-/data/sft.jsonl}
OUT_DIR=${OUT_DIR:-/data/adapter}
S3=${S3_PREFIX:-s3://ai-monitoring-test-123456789012/gh0stx-rft}
NVFP4_S3=${NVFP4_S3:-s3://ai-monitoring-test-123456789012/nvfp4_v4}
mkdir -p "$BASE_DIR" "$(dirname "$DATA")" "$OUT_DIR"

echo "== deps =="
pip install -q --no-input torch --index-url https://download.pytorch.org/whl/cu126 2>&1 | tail -1 || true
pip install -q --no-input "transformers==5.12.1" "compressed-tensors>=0.18" "accelerate>=1.14" \
  "peft>=0.14" "datasets>=3" safetensors awscli 2>&1 | tail -2 || true
python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.version.cuda,'ngpu',torch.cuda.device_count())"

echo "== sync nvfp4 base (84GB) S3 -> $BASE_DIR =="
aws s3 sync "$NVFP4_S3/" "$BASE_DIR/" --only-show-errors
ls "$BASE_DIR"/*.safetensors 2>/dev/null | wc -l
test -f "$BASE_DIR/config.json" || { echo "no config.json"; exit 2; }

echo "== fetch dataset =="
[ -s "$DATA" ] || aws s3 cp "$S3/sft.jsonl" "$DATA"
wc -l "$DATA"

echo "== nvidia =="; nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true
echo "== train =="
python3 /code/train_qlora_nvfp4.py

echo "== upload adapter -> S3 =="
aws s3 sync "$OUT_DIR" "$S3/adapter-nvfp4" --only-show-errors
echo "ALL DONE"
