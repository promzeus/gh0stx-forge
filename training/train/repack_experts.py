#!/usr/bin/env python3
"""One-time base fix: fuse the per-expert MoE weights into the model's native packed
layout so from_pretrained needs no hand-packing patch (which fights deepspeed zero.Init).

Source layout (decensored_v4):  model.language_model.layers.{L}.mlp.experts.{e}.gate_proj.weight  [inter, hidden]
                                                                          .up_proj.weight    [inter, hidden]
                                                                          .down_proj.weight  [hidden, inter]
Target layout (runtime):        ...layers.{L}.mlp.experts.gate_up_proj  [E, 2*inter, hidden]  (cat(gate,up) per expert)
                                ...layers.{L}.mlp.experts.down_proj     [E, hidden, inter]

Everything non-expert (linear_attn, norms, router mlp.gate, shared_expert, embeddings,
lm_head) is copied through byte-for-byte. Writes one safetensors file per layer plus one
for the non-layer tensors, rebuilds the index, copies the aux files, and self-checks.
"""
import os, sys, json, glob, shutil, re, collections
import torch
from safetensors import safe_open
from safetensors.torch import save_file

RAW = sys.argv[1] if len(sys.argv) > 1 else "/base"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/base_fused"
os.makedirs(OUT, exist_ok=True)

idx = json.load(open(os.path.join(RAW, "model.safetensors.index.json")))
wmap = idx["weight_map"]

# open each source shard once
_handles = {}
def h(shard):
    if shard not in _handles:
        _handles[shard] = safe_open(os.path.join(RAW, shard), framework="pt")
    return _handles[shard]
def get(key):
    return h(wmap[key]).get_tensor(key)

EXP = re.compile(r"^(model\.language_model\.layers\.(\d+))\.mlp\.experts\.(\d+)\.(gate_proj|up_proj|down_proj)\.weight$")
# map layer -> num experts, and collect the non-expert keys per file target
experts_by_layer = collections.defaultdict(int)
layer_of_key = {}          # non-expert key -> layer int (or -1 for non-layer tensors)
for k in wmap:
    m = EXP.match(k)
    if m:
        L = int(m.group(2)); experts_by_layer[L] = max(experts_by_layer[L], int(m.group(3)) + 1)
    else:
        lm = re.search(r"^model\.language_model\.layers\.(\d+)\.", k)
        layer_of_key[k] = int(lm.group(1)) if lm else -1

new_index = {}
total_bytes = 0

def emit(fname, tensors):
    global total_bytes
    save_file(tensors, os.path.join(OUT, fname), metadata={"format": "pt"})
    for k in tensors:
        new_index[k] = fname
        total_bytes += tensors[k].numel() * tensors[k].element_size()

layers = sorted(experts_by_layer)
print(f"layers with experts: {len(layers)}  experts/layer: {sorted(set(experts_by_layer.values()))}", flush=True)

for L in layers:
    E = experts_by_layer[L]
    pre = f"model.language_model.layers.{L}.mlp.experts"
    g0 = get(f"{pre}.0.gate_proj.weight")
    inter, hidden = g0.shape
    dt = g0.dtype
    gu = torch.empty(E, 2 * inter, hidden, dtype=dt)
    dp = torch.empty(E, hidden, inter, dtype=dt)
    for e in range(E):
        gu[e] = torch.cat([get(f"{pre}.{e}.gate_proj.weight"), get(f"{pre}.{e}.up_proj.weight")], dim=0)
        dp[e] = get(f"{pre}.{e}.down_proj.weight")
    tensors = {f"{pre}.gate_up_proj": gu, f"{pre}.down_proj": dp}
    # non-expert tensors that live on this layer (attn, norms, router gate, shared_expert)
    for k, lk in layer_of_key.items():
        if lk == L:
            tensors[k] = get(k)
    emit(f"model-layer{L:03d}.safetensors", tensors)
    print(f"  layer {L}: E={E} inter={inter} hidden={hidden} gate_up={tuple(gu.shape)} +{len(tensors)-2} other", flush=True)

# non-layer tensors (embed_tokens, final norm, lm_head, ...) and any layer without experts
rest = {k: get(k) for k, lk in layer_of_key.items() if lk == -1 or lk not in experts_by_layer}
if rest:
    emit("model-extra.safetensors", rest)
    print(f"  extra: {len(rest)} non-layer tensors", flush=True)

json.dump({"metadata": {"total_size": total_bytes}, "weight_map": new_index},
          open(os.path.join(OUT, "model.safetensors.index.json"), "w"))

# copy aux files unchanged (config, tokenizer, generation config, etc.)
for f in glob.glob(os.path.join(RAW, "*")):
    b = os.path.basename(f)
    if b.endswith(".safetensors") or b == "model.safetensors.index.json":
        continue
    if os.path.isfile(f):
        shutil.copy2(f, os.path.join(OUT, b))

# self-check: reload one layer's fused gate_up and verify it equals cat(gate,up) of source
Lc = layers[0]
pre = f"model.language_model.layers.{Lc}.mlp.experts"
fh = {}
fmap = json.load(open(os.path.join(OUT, "model.safetensors.index.json")))["weight_map"]
def fget(key):
    sh = fmap[key]
    if sh not in fh: fh[sh] = safe_open(os.path.join(OUT, sh), framework="pt")
    return fh[sh].get_tensor(key)
gu = fget(f"{pre}.gate_up_proj")
src_g = get(f"{pre}.0.gate_proj.weight"); src_u = get(f"{pre}.0.up_proj.weight")
inter = src_g.shape[0]
ok = torch.equal(gu[0, :inter], src_g) and torch.equal(gu[0, inter:], src_u)
assert ok, "fused gate_up_proj[0] does not match source cat(gate,up)"
# key parity: every non-expert source key present, no per-expert keys leaked
src_nonexp = {k for k in wmap if not EXP.match(k)}
missing = src_nonexp - set(new_index)
leaked = [k for k in new_index if EXP.match(k)]
assert not missing, f"missing non-expert keys: {list(missing)[:5]}"
assert not leaked, f"per-expert keys leaked into fused base: {leaked[:5]}"
print(f"OK fused base -> {OUT}  files={len(set(new_index.values()))}  "
      f"size={total_bytes/1e9:.1f}GB  self-check passed", flush=True)
