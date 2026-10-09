#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
REAP expert-prune for GLM-4.6 (glm4_moe, transformers 5.17): abliterated ~353B -> ~151B so NVFP4
(~85GB) + fp8 KV (120k) fits one GB10 (122GB). Ported from pipeline_reap.py (Qwen).

Verified structure (transformers 5.17.0, meta-instantiated Glm4MoeMoE):
  * experts = Glm4MoeExperts (FUSED, not ModuleList): packed 3D params
      gate_up_proj [n_exp, 2*moe_inter=3072, H=5120]   down_proj [n_exp, H=5120, I=1536]
    forward loops `for expert_idx in expert_hit` using self.num_experts (one_hot num_classes) ->
    MUST set experts.num_experts = n_keep after slicing dim0.
  * gate = Glm4MoeTopkRouter: weight [n_exp, H] Parameter + e_score_correction_bias [n_exp] buffer;
    forward returns (router_logits, topk_weights, topk_indices) -> hook captures the integer indices.
  * shared_experts (always-on) untouched. n_group=1/topk_group=1 -> flat top-k, pruning any experts safe.

Prune = slice packed params [keep] on dim0 + slice gate weight/bias + set num_experts/config. Metric:
per-domain routed-selection COUNT over balanced domain-tagged calib (calib_corpus.py), union round-robin.

Run: CPU big-RAM node (r7i.48xlarge, DEVICE_MAP=cpu). ~705GB bf16 in + ~300GB pruned out.
"""
import os, json, time, gc, collections
import torch, torch.nn as nn
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL     = os.environ.get("MODEL_ID", "/data/abl")
KEEP_FRAC = float(os.environ.get("KEEP_FRAC", "0.40"))            # 64/160 -> ~151B -> NVFP4 ~85GB -> 120k fits
OUT       = os.environ.get("OUT_DIR", "/data"); os.makedirs(OUT, exist_ok=True)
PRUNED    = os.environ.get("PRUNED_DIR", f"{OUT}/glm46-pruned")
IMP       = f"{OUT}/importance_glm.json"
MAXLEN    = int(os.environ.get("MAXLEN", "1024"))
TARGET    = int(os.environ.get("CALIB_TARGET", "128"))
USE_HF    = os.environ.get("USE_HF", "1") == "1"
DEVICE_MAP= os.environ.get("DEVICE_MAP", "cpu")
t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

if os.path.exists(f"{PRUNED}/config.json"):
    log(f"PRUNED already at {PRUNED} — done."); raise SystemExit(0)

import sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calib_corpus import assemble
CORPUS = assemble(target_total=TARGET, use_hf=USE_HF, max_chars=MAXLEN * 3)
DOMAINS = sorted(set(c["domain"] for c in CORPUS))
log(f"calib: {len(CORPUS)} seqs {dict(collections.Counter(c['domain'] for c in CORPUS))}")

log(f"loading {MODEL} (bf16, device_map={DEVICE_MAP}) — 705GB, tens of minutes")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16, device_map=DEVICE_MAP,
                                             low_cpu_mem_usage=True, trust_remote_code=True)
model.eval(); model.requires_grad_(False)
DEV = next(model.parameters()).device
n_params0 = sum(p.numel() for p in model.parameters())
log(f"loaded: {n_params0/1e9:.1f}B params")

# ---- discover routed MoE blocks (fused Glm4MoeExperts: experts.gate_up_proj is 3D) ----
def _get_base(m):
    b = getattr(m, "model", m)
    if hasattr(b, "language_model"): b = b.language_model
    return b
base = _get_base(model)
moe = {}
for li, layer in enumerate(base.layers):
    mlp = getattr(layer, "mlp", None)
    if mlp is None: continue
    ex = getattr(mlp, "experts", None); gt = getattr(mlp, "gate", None)
    gup = getattr(ex, "gate_up_proj", None) if ex is not None else None
    if gup is not None and hasattr(gup, "dim") and gup.dim() == 3 and gt is not None and hasattr(gt, "weight"):
        moe[f"model.layers.{li}.mlp"] = mlp
if not moe:
    log("DISCOVERY FAILED — dumping sample mlp:")
    for li in sorted({3, len(base.layers)//2, len(base.layers)-2}):
        mlp = getattr(base.layers[li], "mlp", None); ex = getattr(mlp, "experts", None)
        log(f"  layer{li}.mlp={type(mlp).__name__} experts={type(ex).__name__} "
            f"gup={getattr(getattr(ex,'gate_up_proj',None),'shape',None)}")
    raise SystemExit("discovery failed")
n_exp = moe[next(iter(moe))].experts.gate_up_proj.shape[0]
top_k = int(getattr(model.config, "num_experts_per_tok", 8))
log(f"{len(moe)} routed MoE blocks, {n_exp} experts/block, top_k={top_k} (fused Glm4MoeExperts, shared excluded)")

if os.path.exists(IMP):
    log("importance_glm.json exists — reusing"); plan = json.load(open(IMP))["keep_idx"]
else:
    log("PHASE A: per-domain expert selection counts (capture router topk_indices)")
    counts = {n: {d: torch.zeros(n_exp, dtype=torch.long) for d in DOMAINS} for n in moe}
    cur_dom = {"d": None}
    def mk_hook(name):
        def hook(module, inp, out):
            idx = None                                   # router returns (logits, topk_weights, topk_indices)
            if isinstance(out, (tuple, list)):
                for t in out:                            # pick the integer index tensor
                    if torch.is_tensor(t) and not t.is_floating_point():
                        idx = t; break
            if idx is None: return
            counts[name][cur_dom["d"]] += torch.bincount(idx.reshape(-1).to(torch.long).cpu(), minlength=n_exp)
        return hook
    hooks = [m.gate.register_forward_hook(mk_hook(n)) for n, m in moe.items()]
    with torch.no_grad():
        for i, item in enumerate(CORPUS):
            cur_dom["d"] = item["domain"]
            ids = tok(item["text"], return_tensors="pt", truncation=True, max_length=MAXLEN).to(DEV)
            model(**ids)
            if i % 20 == 0: log(f"  calib {i+1}/{len(CORPUS)} ({item['domain']})")
    for h in hooks: h.remove()

    for d in DOMAINS:
        tot = int(sum(counts[n][d].sum() for n in moe))
        log(f"  domain '{d}': total routed selections = {tot}")
        assert tot > 0, f"domain {d} got ZERO routing — calib gap or hook missed indices!"

    keep_g = max(top_k, int(round(n_exp * KEEP_FRAC)))
    plan, summ = {}, {}
    for name in moe:
        ranked = {d: counts[name][d].argsort(descending=True).tolist() for d in DOMAINS}
        keep, ptrs = set(), {d: 0 for d in DOMAINS}
        while len(keep) < keep_g:
            progressed = False
            for d in DOMAINS:
                while ptrs[d] < n_exp:
                    e = ranked[d][ptrs[d]]; ptrs[d] += 1
                    if counts[name][d][e] > 0 and e not in keep:
                        keep.add(e); progressed = True; break
                if len(keep) >= keep_g: break
            if not progressed: break
        if len(keep) < keep_g:
            tot = sum(counts[name][d] for d in DOMAINS)
            for e in tot.argsort(descending=True).tolist():
                if e not in keep: keep.add(e)
                if len(keep) >= keep_g: break
        plan[name] = sorted(keep)
        summ[name] = {"keep": len(plan[name]), "dead": int((sum(counts[name][d] for d in DOMAINS) == 0).sum())}
    json.dump({"model": MODEL, "keep_frac": KEEP_FRAC, "top_k": top_k, "domains": DOMAINS,
               "keep_idx": plan, "summary": summ}, open(IMP, "w"))
    log(f"PHASE A done. keep/block={keep_g}; saved {IMP}")

# PHASE B: slice packed expert params + gate weight/bias; shared_experts untouched
log(f"PHASE B: prune to {KEEP_FRAC:.0%} experts/block (packed slice on dim0)")
for name, m in moe.items():
    keep = plan[name] if isinstance(plan[name], list) else sorted(plan[name])
    assert max(keep) < n_exp and min(keep) >= 0, f"{name}: keep idx out of range"
    ex, gt = m.experts, m.gate
    with torch.no_grad():
        ex.gate_up_proj = nn.Parameter(ex.gate_up_proj.data[keep].clone(), requires_grad=False)
        ex.down_proj    = nn.Parameter(ex.down_proj.data[keep].clone(),    requires_grad=False)
        gt.weight       = nn.Parameter(gt.weight.data[keep].clone(),       requires_grad=False)
        b = getattr(gt, "e_score_correction_bias", None)
        if b is not None:
            gt.e_score_correction_bias = b.data[keep].clone()            # registered buffer -> Tensor
    ex.num_experts = len(keep)                                           # forward one_hot num_classes
    for obj in (m, gt):
        for attr in ("n_routed_experts", "num_experts"):
            if hasattr(obj, attr): setattr(obj, attr, len(keep))
    gc.collect()
keep_n = len(plan[next(iter(plan))])
for attr in ("n_routed_experts", "num_experts", "num_local_experts"):
    if hasattr(model.config, attr): setattr(model.config, attr, keep_n)
n_after = sum(p.numel() for p in model.parameters())
log(f"  params {n_params0/1e9:.1f}B -> {n_after/1e9:.1f}B | experts/block {n_exp}->{keep_n}")

log("sanity gens on pruned model (RU/code/math/offsec)")
for probe in ["Столица Франции —", "def is_prime(n):", "17 + 25 =", "reverse shell на Python:"]:
    with torch.no_grad():
        ids = tok(probe, return_tensors="pt").to(DEV)
        out = model.generate(**ids, max_new_tokens=24, do_sample=False, pad_token_id=tok.eos_token_id)
    log(f"  GEN [{probe}] -> {tok.decode(out[0], skip_special_tokens=True)[:110]!r}")

log(f"saving pruned bf16 to {PRUNED} (max_shard_size=20GB)")
model.save_pretrained(PRUNED, safe_serialization=True, max_shard_size="20GB")
tok.save_pretrained(PRUNED)
json.dump({"params_before": n_params0, "params_after": n_after,
           "experts_per_block": [n_exp, keep_n], "keep_frac": KEEP_FRAC}, open(f"{OUT}/prune_result_glm.json", "w"))
log("GLM PRUNE COMPLETE.")
