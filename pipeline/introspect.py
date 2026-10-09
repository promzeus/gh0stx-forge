#!/usr/bin/env python3
import os, torch
from transformers import AutoModelForCausalLM, AutoConfig
MODEL = os.environ.get("MODEL_ID", "Qwen/Qwen3-30B-A3B")
cfg = AutoConfig.from_pretrained(MODEL)
keys = [k for k in dir(cfg) if ("expert" in k.lower() or k in
        ("num_hidden_layers","hidden_size","num_experts","num_experts_per_tok",
         "moe_intermediate_size","decoder_sparse_step","mlp_only_layers"))]
print("CONFIG:", {k: getattr(cfg, k) for k in keys}, flush=True)
m = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16, device_map="auto")
for i in range(min(3, cfg.num_hidden_layers)):
    mlp = m.model.layers[i].mlp
    print(f"\nLAYER {i} mlp = {type(mlp).__name__}", flush=True)
    for n, c in mlp.named_children():
        w = f" weight={tuple(c.weight.shape)}" if hasattr(c, "weight") and c.weight is not None else ""
        ln = f" len={len(c)}" if hasattr(c, "__len__") else ""
        print(f"  .{n}: {type(c).__name__}{w}{ln}", flush=True)
        for pn, p in c.named_parameters(recurse=False):
            print(f"      param {pn}: {tuple(p.shape)}", flush=True)
        # one level deeper for ModuleList/experts container
        if ln:
            for sn, sc in list(c.named_children())[:1]:
                print(f"      [{sn}] {type(sc).__name__}", flush=True)
                for spn, sp in sc.named_parameters(recurse=True):
                    print(f"          {spn}: {tuple(sp.shape)}", flush=True)
print("\n=== MoE-ish module classes ===", flush=True)
seen = set()
for name, mod in m.named_modules():
    cn = type(mod).__name__
    if any(s in cn for s in ("Sparse", "Moe", "MoE", "Expert", "Router", "Gate")) and cn not in seen:
        seen.add(cn)
        attrs = [a for a in ("experts", "gate", "router", "gate_up_proj", "down_proj",
                             "num_experts", "top_k") if hasattr(mod, a)]
        print(f"{cn}: attrs={attrs}", flush=True)
print("INTROSPECT DONE", flush=True)
