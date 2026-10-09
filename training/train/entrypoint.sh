#!/usr/bin/env bash
# gh0stx QLoRA training entrypoint. Runs inside the GPU pod on the example-cluster cluster.
set -euo pipefail
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1

BASE_DIR=${BASE_DIR:-/workspace/run397b_v3/decensored_v4}
DATA=${DATA:-/workspace/gh0stx-rft/sft.jsonl}
OUT_DIR=${OUT_DIR:-/workspace/gh0stx-rft/adapter}
S3=${S3_PREFIX:-s3://ai-monitoring-test-123456789012/gh0stx-rft}
mkdir -p "$(dirname "$DATA")" "$OUT_DIR"

echo "== deps =="
# CUDA torch wheel bundles its own CUDA userspace; the node GPU AMI provides the driver.
pip install -q --no-input torch --index-url https://download.pytorch.org/whl/cu126 2>&1 | tail -2 || true
pip install -q --no-input \
  "transformers==5.12.1" "accelerate>=1.14" "peft>=0.14" "trl>=0.12" \
  "bitsandbytes>=0.47" "datasets>=3" "safetensors" "numpy" "awscli" \
  "deepspeed>=0.15" 2>&1 | tail -3 || true
python3 -c "import torch;print('torch',torch.__version__,'cuda',torch.version.cuda,'avail',torch.cuda.is_available(),'ngpu',torch.cuda.device_count())"

echo "== fetch dataset =="
if [ ! -s "$DATA" ]; then aws s3 cp "${DATA_S3:-$S3/sft.jsonl}" "$DATA"; fi
wc -l "$DATA"

BASE_S3=${BASE_S3:-s3://ai-monitoring-test-123456789012/decensored_v4}
FUSED_S3=${FUSED_S3:-s3://ai-monitoring-test-123456789012/decensored_v4_fused}
if [ "${ZERO3:-0}" = "1" ] || [ "${ORPO:-0}" = "1" ]; then
  # ZeRO-3 (and ORPO, which reuses the ZeRO-3 path) needs the FUSED base at BASE_DIR (experts
  # packed) so from_pretrained loads with no hand-packing patch. Prefer prebuilt fused in S3.
  if [ ! -f "$BASE_DIR/model.safetensors.index.json" ]; then
    if aws s3 ls "$FUSED_S3/model.safetensors.index.json" >/dev/null 2>&1; then
      echo "== download prebuilt fused base =="
      aws s3 sync "$FUSED_S3/" "$BASE_DIR/" --only-show-errors
    else
      echo "== fused base absent: download raw base + repack (one-time) =="
      RAW=${RAW_DIR:-/work/base_raw}
      mkdir -p "$RAW"
      aws s3 sync "$BASE_S3/" "$RAW/" --only-show-errors
      python3 /code/repack_experts.py "$RAW" "$BASE_DIR"
      echo "== upload fused base to S3 for reuse =="
      aws s3 sync "$BASE_DIR/" "$FUSED_S3/" --only-show-errors
      rm -rf "$RAW"
    fi
  fi
else
  echo "== sync raw base from S3 if absent (multi-AZ: base rides local disk, no PVC) =="
  if [ ! -f "$BASE_DIR/model.safetensors.index.json" ]; then
    aws s3 sync "$BASE_S3/" "$BASE_DIR/" --only-show-errors
  fi
fi
echo "== base sanity =="
test -f "$BASE_DIR/model.safetensors.index.json" || { echo "base index missing at $BASE_DIR"; exit 2; }
python3 - <<'PY'
import json,glob,os
b=os.environ["BASE_DIR"]
idx=json.load(open(f"{b}/model.safetensors.index.json"))
sh=sorted(set(idx["weight_map"].values()))
have=sorted(os.path.basename(x) for x in glob.glob(f"{b}/*.safetensors"))
miss=[s for s in sh if s not in have]
print(f"shards expected={len(sh)} present={len(have)} missing={miss}")
assert not miss, "missing shards"
PY

echo "== nvidia =="
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader || true

echo "== train (periodic S3 checkpoint sync for spot durability) =="
# push OUT_DIR to S3 every 120s so a spot reclaim mid-run leaves the latest checkpoint in S3
( while sleep 120; do aws s3 sync "$OUT_DIR" "$S3/adapter" --only-show-errors --delete 2>/dev/null || true; done ) &
BGSYNC=$!
if [ "${ZERO3:-0}" = "1" ] || [ "${ORPO:-0}" = "1" ]; then
  NPROC=${NPROC_PER_NODE:-$(nvidia-smi -L | wc -l)}
  echo "== torchrun --nproc_per_node=$NPROC (deepspeed stage-3; ORPO=${ORPO:-0}) =="
  torchrun --standalone --nproc_per_node="$NPROC" /code/train_qlora.py
else
  python3 /code/train_qlora.py
fi
kill "$BGSYNC" 2>/dev/null || true

echo "== upload adapter -> S3 =="
aws s3 sync "$OUT_DIR" "$S3/adapter" --only-show-errors

if [ -n "${MERGED_DIR:-}" ] && [ -f "$OUT_DIR/DONE" ]; then
  echo "== merge adapter into fused base (rank0, CPU) =="
  MERGED_DIR="$MERGED_DIR" python3 /code/merge_adapter.py
  echo "== upload merged -> S3 =="
  aws s3 sync "$MERGED_DIR" "${MERGED_S3:-s3://ai-monitoring-test-123456789012/gh0stx-rft/merged}" --only-show-errors
fi
echo "ALL DONE"
