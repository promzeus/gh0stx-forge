#!/usr/bin/env python3
"""Build a DFlash-only release: trunk without the MTP head.

Hardlinks trunk shards from the source release, drops model-mtp-quantized.safetensors,
strips mtp.* keys from index.json and removes mtp_num_hidden_layers from config.json so
vLLM does not instantiate/load the MTP module (dead weight when serving with DFlash).
"""
import json, os, shutil, sys

SRC = "/home/gh0stx/nvfp4_v4_releases/calibrated_mtpq_v1"
DST = "/home/gh0stx/nvfp4_v4_releases/dflash_only"
MTP_SHARD = "model-mtp-quantized.safetensors"

_DT = {"F64":8,"F32":4,"F16":2,"BF16":2,"I64":8,"I32":4,"I16":2,"I8":1,"U8":1,
       "F8_E4M3":1,"F8_E5M2":1,"BOOL":1}

if os.path.exists(DST):
    print(f"{DST} уже существует"); sys.exit(1)
os.makedirs(DST)

for fn in os.listdir(SRC):
    src = os.path.join(SRC, fn)
    if not os.path.isfile(src):
        continue
    if fn in (MTP_SHARD, "config.json", "model.safetensors.index.json", "CHECKSUMS.sha256"):
        continue
    if fn.endswith(".bak"):
        continue
    try:
        os.link(src, os.path.join(DST, fn))
    except OSError:
        shutil.copy2(src, os.path.join(DST, fn))

idx = json.load(open(os.path.join(SRC, "model.safetensors.index.json")))
wm = {k: v for k, v in idx["weight_map"].items() if not k.startswith("mtp.")}
dropped = len(idx["weight_map"]) - len(wm)
assert MTP_SHARD not in set(wm.values()), "mtp-шард всё ещё в weight_map"

from safetensors import safe_open
total = 0
for sh in sorted(set(wm.values())):
    with safe_open(os.path.join(DST, sh), "pt") as f:
        for k in f.keys():
            sl = f.get_slice(k); n = 1
            for d in sl.get_shape(): n *= d
            total += n * _DT[sl.get_dtype()]
idx["weight_map"] = wm
idx.setdefault("metadata", {})["total_size"] = total
json.dump(idx, open(os.path.join(DST, "model.safetensors.index.json"), "w"), indent=2)

cfg = json.load(open(os.path.join(SRC, "config.json")))
removed = []
for holder, name in ((cfg, "top"), (cfg.get("text_config", {}), "text_config")):
    if isinstance(holder, dict) and "mtp_num_hidden_layers" in holder:
        holder.pop("mtp_num_hidden_layers"); removed.append(name)
json.dump(cfg, open(os.path.join(DST, "config.json"), "w"), indent=2)

print(f"ключей mtp.* убрано: {dropped}")
print(f"mtp_num_hidden_layers убран из: {removed or 'нигде не было'}")
print(f"total_size: {total}")
print(f"файлов в релизе: {len(os.listdir(DST))}")
