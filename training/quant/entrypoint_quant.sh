#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
S3=${S3_PREFIX:-s3://ai-monitoring-test-123456789012/gh0stx-rft}
echo "== deps =="
pip install -q --no-input torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -1 || true
pip install -q --no-input "compressed-tensors==0.18.0" "safetensors" "awscli" 2>&1 | tail -1 || true
python3 -c "import compressed_tensors as c; print('compressed-tensors', c.__version__)"
mkdir -p /merged /dflash /graft_out
echo "== sync dflash_meta (input_global_scale + config) first =="
aws s3 sync "$S3/dflash_meta/" /dflash/ --only-show-errors
echo "== preflight: graft imports + scale keys (fail fast before the 262GB sync) =="
python3 - <<'PY'
import json
from compressed_tensors.quantization import preset_name_to_scheme
from compressed_tensors.quantization.lifecycle.forward import quantize
from compressed_tensors.quantization.utils.helpers import calculate_qparams, generate_gparam
from compressed_tensors.compressors import pack_fp4_to_uint8
wmap = json.load(open("/dflash/model.safetensors.index.json"))["weight_map"]
ngs = sum(k.endswith(".weight_global_scale") for k in wmap)
nis = sum(k.endswith(".input_global_scale") for k in wmap)
assert ngs and nis, f"missing scales in dflash_meta: wgs={ngs} igs={nis}"
print(f"graft imports OK; weight_global_scale={ngs} input_global_scale={nis}")
PY
echo "== sync merged bf16 (~262GB) =="
aws s3 sync "$S3/merged/" /merged/ --only-show-errors
echo "== graft-quant =="
MODE=quant MERGED_DIR=/merged DFLASH_DIR=/dflash OUT_DIR=/graft_out python3 /code/graft_requant.py
echo "== upload graft shard =="
aws s3 cp /graft_out/model-graft-00001.safetensors "$S3/graft/model-graft-00001.safetensors" --only-show-errors
aws s3 cp /graft_out/graft_keys.json "$S3/graft/graft_keys.json" --only-show-errors
echo "ALL DONE"
