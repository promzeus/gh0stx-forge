#!/usr/bin/env bash
set -euo pipefail
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1
BASE_DIR=${BASE_DIR:-/base}; ADAPTER_DIR=${ADAPTER_DIR:-/adapter}; MERGED_DIR=${MERGED_DIR:-/merged}
S3=${S3_PREFIX:-s3://ai-monitoring-test-123456789012/gh0stx-rft}
echo "== deps =="
pip install -q --no-input torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -1 || true
pip install -q --no-input "transformers==5.12.1" "accelerate>=1.14" "peft>=0.14" "safetensors" "awscli" 2>&1 | tail -2 || true
echo "== sync base (bf16, ~262GB) from S3 =="
if [ ! -f "$BASE_DIR/model.safetensors.index.json" ]; then
  aws s3 sync "${BASE_S3:-s3://ai-monitoring-test-123456789012/decensored_v4}/" "$BASE_DIR/" --only-show-errors
fi
echo "== fetch adapter =="
aws s3 sync "$S3/adapter-final/" "$ADAPTER_DIR/" --only-show-errors
echo "== merge =="
python3 /code/merge_adapter.py
echo "== upload merged bf16 -> S3 =="
aws s3 sync "$MERGED_DIR" "$S3/merged" --only-show-errors
echo "ALL DONE"
