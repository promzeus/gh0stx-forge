#!/usr/bin/env bash
# One-time base fix on a CPU node: fuse per-expert MoE weights -> packed layout, upload to S3.
set -euo pipefail
export PYTHONUNBUFFERED=1
BASE_S3=${BASE_S3:-s3://ai-monitoring-test-123456789012/decensored_v4}
FUSED_S3=${FUSED_S3:-s3://ai-monitoring-test-123456789012/decensored_v4_fused}
RAW=${RAW_DIR:-/work/raw}
FUSED=${FUSED_DIR:-/work/fused}
mkdir -p "$RAW" "$FUSED"

echo "== deps (CPU torch) =="
pip install -q --no-input torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -1 || true
pip install -q --no-input numpy safetensors "awscli" 2>&1 | tail -1 || true
python3 -c "import torch,safetensors,numpy;print('torch',torch.__version__,'np',numpy.__version__)"

echo "== preflight: bf16 safetensors write/read (fail fast before the 270GB download) =="
python3 - <<'PY'
import torch
from safetensors.torch import save_file, safe_open
t = torch.randn(4, 8, dtype=torch.bfloat16)
save_file({"a": t}, "/tmp/_bf16.safetensors", metadata={"format": "pt"})
with safe_open("/tmp/_bf16.safetensors", framework="pt") as f:
    assert torch.equal(f.get_tensor("a"), t), "bf16 roundtrip mismatch"
print("preflight bf16 OK")
PY

if aws s3 ls "$FUSED_S3/model.safetensors.index.json" >/dev/null 2>&1; then
  echo "fused base already present at $FUSED_S3 — nothing to do"; echo REPACK_DONE; exit 0
fi

echo "== download raw base =="
aws s3 sync "$BASE_S3/" "$RAW/" --only-show-errors
echo "== repack =="
python3 /code/repack_experts.py "$RAW" "$FUSED"
echo "== upload fused base =="
aws s3 sync "$FUSED/" "$FUSED_S3/" --only-show-errors
echo REPACK_DONE
