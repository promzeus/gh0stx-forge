#!/usr/bin/env python3
"""Stage 6 — graft-requant, two modes.

LoRA touched only attention (q/k/v/o on full_attention layers), shared_expert (gate/up/down,
every layer) and linear_attn (in_proj_qkv/a/b/z, out_proj on linear_attention layers). Everything
else — the 262GB of routed experts, embeddings, lm_head, norms, router, conv1d — is untouched and
comes straight from the deployed dflash_only release.

MODE=quant  (AWS node: has merged bf16 + dflash_only): re-quantize the changed attention/
  shared_expert weights to NVFP4 with compressed-tensors' own primitives (group_size 16, fp8 e4m3
  group scales, f32 global), keeping dflash_only's input_global_scale (activation stats unchanged);
  copy the changed linear_attn weights as bf16. Writes ONE graft shard with just the changed
  tensors (~18GB) -> upload that to S3.
MODE=assemble  (box: has local dflash_only + the downloaded graft shard): hardlink dflash_only's
  shards, drop in the graft shard, rewrite the index so changed keys point to the graft shard.
  Produces the new release with no large download.

Env: MODE, MERGED_DIR, DFLASH_DIR, OUT_DIR, GRAFT_SHARD (assemble mode: path to the shard).
"""
import os, json, time, shutil
import torch
from safetensors import safe_open
from safetensors.torch import save_file

MODE       = os.environ.get("MODE", "quant")
MERGED_DIR = os.environ.get("MERGED_DIR", "/merged")
DFLASH_DIR = os.environ.get("DFLASH_DIR", "/dflash")
OUT_DIR    = os.environ.get("OUT_DIR", "/graft_out")
GRAFT_SHARD_NAME = "model-graft-00001.safetensors"
PFX = "model.language_model.layers"

t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

class ShardReader:
    def __init__(self, base):
        self.base = base
        self.wmap = json.load(open(os.path.join(base, "model.safetensors.index.json")))["weight_map"]
        self._open = {}
    def get(self, key):
        sh = self.wmap[key]
        if sh not in self._open:
            self._open[sh] = safe_open(os.path.join(self.base, sh), framework="pt")
        return self._open[sh].get_tensor(key)

def changed_module_map(dflash_dir):
    cfg = json.load(open(os.path.join(dflash_dir, "config.json")))
    tc = cfg.get("text_config") or cfg
    ltypes, nlayers = tc["layer_types"], tc["num_hidden_layers"]
    quant, copy = [], []
    for L in range(nlayers):
        for m in ("mlp.shared_expert.gate_proj", "mlp.shared_expert.up_proj", "mlp.shared_expert.down_proj"):
            quant.append(f"{PFX}.{L}.{m}")
        if ltypes[L] == "full_attention":
            for m in ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj"):
                quant.append(f"{PFX}.{L}.{m}")
        if ltypes[L] == "linear_attention":
            for m in ("linear_attn.in_proj_qkv", "linear_attn.in_proj_a", "linear_attn.in_proj_b",
                      "linear_attn.in_proj_z", "linear_attn.out_proj"):
                copy.append(f"{PFX}.{L}.{m}")
    return quant, copy

def run_quant():
    from compressed_tensors.quantization import preset_name_to_scheme
    from compressed_tensors.quantization.lifecycle.forward import quantize
    from compressed_tensors.quantization.utils.helpers import calculate_qparams, generate_gparam
    from compressed_tensors.compressors import pack_fp4_to_uint8
    wargs = preset_name_to_scheme("NVFP4", ["Linear"]).weights
    GS = wargs.group_size
    def quant_pack(W, gs):
        # gs = dflash's weight_global_scale, NOT recomputed: the W4A4 kernel pairs
        # weight_global_scale with the (reused) input_global_scale; recomputing it desyncs
        # that pairing and produces garbage even for near-identical weights.
        Wf = W.float(); out, inn = Wf.shape
        g = Wf.view(out, inn // GS, GS)
        scale, zp = calculate_qparams(g.amin(-1), g.amax(-1), wargs, global_scale=gs)
        q = quantize(x=W, scale=scale, zero_point=zp, args=wargs, global_scale=gs)
        return pack_fp4_to_uint8(q), scale.to(torch.float8_e4m3fn)

    os.makedirs(OUT_DIR, exist_ok=True)
    mrg, dfl = ShardReader(MERGED_DIR), ShardReader(DFLASH_DIR)
    def mkey(k): return k  # merged keeps the ConditionalGeneration prefix model.language_model.layers
    quant_mods, copy_mods = changed_module_map(DFLASH_DIR)
    out = {}
    for i, base in enumerate(quant_mods):
        W = mrg.get(mkey(base + ".weight")).to(torch.bfloat16)
        wgs = dfl.get(base + ".weight_global_scale")
        packed, wscale = quant_pack(W, wgs)
        out[base + ".weight_packed"] = packed
        out[base + ".weight_scale"] = wscale
        out[base + ".weight_global_scale"] = wgs
        out[base + ".input_global_scale"] = dfl.get(base + ".input_global_scale")
        if i % 50 == 0: log(f"quant {i}/{len(quant_mods)}")
    for base in copy_mods:
        out[base + ".weight"] = mrg.get(mkey(base + ".weight")).to(torch.bfloat16)
    log(f"quantized {len(quant_mods)} modules, copied {len(copy_mods)}; {len(out)} tensors")
    save_file({k: v.contiguous() for k, v in out.items()},
              os.path.join(OUT_DIR, GRAFT_SHARD_NAME), metadata={"format": "pt"})
    json.dump(sorted(out.keys()), open(os.path.join(OUT_DIR, "graft_keys.json"), "w"))
    log(f"wrote {GRAFT_SHARD_NAME} ({os.path.getsize(os.path.join(OUT_DIR, GRAFT_SHARD_NAME))/1e9:.1f} GB)")

def run_assemble():
    os.makedirs(OUT_DIR, exist_ok=True)
    graft_path = os.environ.get("GRAFT_SHARD", os.path.join(OUT_DIR, GRAFT_SHARD_NAME))
    dfl_idx = json.load(open(os.path.join(DFLASH_DIR, "model.safetensors.index.json")))["weight_map"]
    with safe_open(graft_path, framework="pt") as f:
        changed = set(f.keys())
    # hardlink dflash shards + aux, drop in graft shard
    for sh in sorted(set(dfl_idx.values())):
        dst = os.path.join(OUT_DIR, sh)
        if not os.path.exists(dst):
            try: os.link(os.path.join(DFLASH_DIR, sh), dst)
            except OSError: shutil.copy(os.path.join(DFLASH_DIR, sh), dst)
    if os.path.abspath(graft_path) != os.path.abspath(os.path.join(OUT_DIR, GRAFT_SHARD_NAME)):
        shutil.copy(graft_path, os.path.join(OUT_DIR, GRAFT_SHARD_NAME))
    for fn in os.listdir(DFLASH_DIR):
        if fn.endswith((".json", ".jinja", ".py", ".txt")) and fn != "model.safetensors.index.json":
            shutil.copy(os.path.join(DFLASH_DIR, fn), os.path.join(OUT_DIR, fn))
    new_map = dict(dfl_idx)
    for k in changed: new_map[k] = GRAFT_SHARD_NAME
    total = sum(os.path.getsize(os.path.join(OUT_DIR, sh)) for sh in set(new_map.values()))
    json.dump({"metadata": {"total_size": total}, "weight_map": new_map},
              open(os.path.join(OUT_DIR, "model.safetensors.index.json"), "w"))
    log(f"assembled: {len(new_map)} keys, {len(set(new_map.values()))} shards, {len(changed)} grafted")
    open(os.path.join(OUT_DIR, "DONE"), "w").write("assembled\n")

if __name__ == "__main__":
    (run_quant if MODE == "quant" else run_assemble)()
    log(f"DONE mode={MODE} -> {OUT_DIR}")
