#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RE-PRUNE Qwen3.5-397B-A17B -> ~149B, fixing the broken prune.
Root-cause fix = BALANCED domain-tagged calibration (calib_corpus.py) + PER-DOMAIN
scoring with UNION-of-per-domain-topK + per-domain FLOOR, so RU/math/precision/offsec
experts survive instead of being starved by a global frequency count on EN-only text.

Metric: per-domain routed-selection COUNT (CPU-feasible; the balanced calib is the
primary fix — research: "calibration is the root cause, metric is secondary"). REAP
saliency (gate x output-norm) needs per-token expert-output norms = infeasible on CPU
for 397B (~tens of hours); left as a GPU future-flag (REAP_SALIENCY=1, not implemented
for CPU). Union+floor mitigates raw-count fragility.

Keeps: shared_expert is NEVER in the routed set (asserted). Resumable (importance.json).
Run: CPU big-RAM node (r7i, DEVICE_MAP=cpu) — 751GB BF16 fits 1.5TB; ~760GB peak.
"""
import os, json, time, gc, collections
import torch, torch.nn as nn, torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL     = os.environ.get("MODEL_ID", "Qwen/Qwen3.5-397B-A17B")
KEEP_FRAC = float(os.environ.get("KEEP_FRAC", "0.36"))     # ~184/512; bump to 0.45 if per-capability drops
FLOOR_FRAC= float(os.environ.get("FLOOR_FRAC", "0.10"))    # per-domain guaranteed min per block (of n_experts)
OUT       = os.environ.get("OUT_DIR", "/workspace/run397b_v2"); os.makedirs(OUT, exist_ok=True)
VER       = os.environ.get("PRUNE_VER", "v2")   # v3 re-prune: KEEP_FRAC 0.29 + offsec-heavy corpus
PRUNED    = f"{OUT}/pruned_{VER}"
IMP       = f"{OUT}/importance_{VER}.json"
MAXLEN    = int(os.environ.get("MAXLEN", "2048"))
TARGET    = int(os.environ.get("CALIB_TARGET", "800"))
USE_HF    = os.environ.get("USE_HF", "1") == "1"
t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

if os.path.exists(f"{PRUNED}/config.json"):
    log(f"PRUNED already at {PRUNED} — done."); raise SystemExit(0)

from calib_corpus import assemble
CORPUS = assemble(target_total=TARGET, use_hf=USE_HF, max_chars=MAXLEN*3)
DOMAINS = sorted(set(c["domain"] for c in CORPUS))
bycount = collections.Counter(c["domain"] for c in CORPUS)
log(f"calib: {len(CORPUS)} seqs across {DOMAINS} -> {dict(bycount)}")

DEVICE_MAP = os.environ.get("DEVICE_MAP", "cpu")
log(f"loading {MODEL} (bf16, device_map={DEVICE_MAP}) — large, tens of minutes")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16, device_map=DEVICE_MAP,
                                             low_cpu_mem_usage=True, trust_remote_code=True)
model.eval()
DEV = next(model.parameters()).device
n_params0 = sum(p.numel() for p in model.parameters())
log(f"loaded: {n_params0/1e9:.1f}B params")

# ---- discover ROUTED MoE blocks (exclude shared_expert) ----
moe = {}
for name, mod in model.named_modules():
    ex = getattr(mod, "experts", None); gt = getattr(mod, "gate", None)
    if ex is not None and hasattr(ex, "gate_up_proj") and gt is not None and hasattr(gt, "weight"):
        assert "shared" not in name.lower(), f"shared expert leaked into routed set: {name}"
        moe[name] = mod
assert moe, "no MoE blocks found"
n_exp = moe[next(iter(moe))].experts.gate_up_proj.shape[0]
top_k = int(getattr(moe[next(iter(moe))].gate, "top_k", 10))
log(f"{len(moe)} routed MoE blocks, {n_exp} experts/block, top_k={top_k} (shared_expert excluded)")

if os.path.exists(IMP):
    log("importance_v2.json exists — reusing")
    saved = json.load(open(IMP)); plan = saved["keep_idx"]
else:
    # PHASE A: PER-DOMAIN selection counts
    log("PHASE A: per-domain expert selection counts over balanced calib")
    # counts[name][domain] = LongTensor[n_exp]
    counts = {n: {d: torch.zeros(n_exp, dtype=torch.long) for d in DOMAINS} for n in moe}
    cur_dom = {"d": None}
    def mk_hook(name):
        def hook(module, inp, out):
            h = inp[0]
            if h.dim() == 3: h = h.reshape(-1, h.shape[-1])
            W = module.weight
            logits = F.linear(h.to(W.dtype), W)
            k = min(int(getattr(module, "top_k", top_k)), logits.shape[-1])
            idx = logits.float().topk(k, dim=-1).indices.reshape(-1).cpu()
            counts[name][cur_dom["d"]] += torch.bincount(idx, minlength=n_exp)
        return hook
    hooks = [m.gate.register_forward_hook(mk_hook(n)) for n, m in moe.items()]
    with torch.no_grad():
        for i, item in enumerate(CORPUS):
            cur_dom["d"] = item["domain"]
            ids = tok(item["text"], return_tensors="pt", truncation=True, max_length=MAXLEN).to(DEV)
            model(**ids)
            if i % 25 == 0: log(f"  calib {i+1}/{len(CORPUS)} ({item['domain']})")
    for h in hooks: h.remove()

    # coverage assert: every domain must route to experts (else calib doesn't cover it)
    for d in DOMAINS:
        tot = int(sum(counts[n][d].sum() for n in moe))
        log(f"  domain '{d}': total routed selections = {tot}")
        assert tot > 0, f"domain {d} got ZERO routing — calib doesn't cover it!"

    # PHASE A keep-plan: fair round-robin UNION of per-domain top-K (this inherently gives
    # each domain a floor of ~keep_g/n_domains, the anti-starvation guarantee — no separate
    # floor step needed; a fixed FLOOR_FRAC*n_domains would overflow the budget).
    keep_g = max(top_k, int(round(n_exp * KEEP_FRAC)))
    plan, summ = {}, {}
    for name in moe:
        ranked = {d: counts[name][d].argsort(descending=True).tolist() for d in DOMAINS}
        keep, ptrs = set(), {d: 0 for d in DOMAINS}
        # round-robin: each domain contributes its next-highest LIVE expert per round
        while len(keep) < keep_g:
            progressed = False
            for d in DOMAINS:
                while ptrs[d] < n_exp:
                    e = ranked[d][ptrs[d]]; ptrs[d] += 1
                    if counts[name][d][e] > 0 and e not in keep:
                        keep.add(e); progressed = True; break
                if len(keep) >= keep_g: break
            if not progressed: break   # all live experts across all domains exhausted
        # pad with global-top-by-total if many dead experts left us under budget
        if len(keep) < keep_g:
            tot = sum(counts[name][d] for d in DOMAINS)
            for e in tot.argsort(descending=True).tolist():
                if e not in keep: keep.add(e)
                if len(keep) >= keep_g: break
        plan[name] = sorted(keep)
        dead = int((sum(counts[name][d] for d in DOMAINS) == 0).sum())
        summ[name] = {"keep": len(plan[name]), "dead": dead}
    # per-domain activation diagnostics: breadth = сколько РАЗНЫХ экспертов зажигает каждый домен
    # (проверка гипотезы "offsec активировал слишком много" + ранг math-экспертов)
    dom_stats = {}
    for d in DOMAINS:
        pbd = [int((counts[name][d] > 0).sum()) for name in moe]  # distinct experts/block
        dom_stats[d] = {"total_selections": int(sum(int(counts[name][d].sum()) for name in moe)),
                        "avg_distinct_experts_per_block": round(sum(pbd)/len(pbd), 1),
                        "max_distinct_per_block": max(pbd), "min_distinct_per_block": min(pbd)}
        log(f"  [activation] {d}: avg {dom_stats[d]['avg_distinct_experts_per_block']} distinct exp/block "
            f"(of {n_exp}), total_sel={dom_stats[d]['total_selections']}")
    json.dump({"model": MODEL, "keep_frac": KEEP_FRAC, "floor_frac": FLOOR_FRAC, "top_k": top_k,
               "domains": DOMAINS, "keep_idx": {k: v for k, v in plan.items()}, "summary": summ,
               "domain_activation": dom_stats},
              open(IMP, "w"))
    # сырые per-block per-domain counts (для глубокого анализа: пересечения, ранги)
    json.dump({name: {d: counts[name][d].tolist() for d in DOMAINS} for name in moe},
              open(f"{OUT}/counts_{VER}.json", "w"))
    log(f"PHASE A done. keep/block={keep_g} floor_frac={FLOOR_FRAC}; saved {IMP} + counts_{VER}.json")

# PHASE B: prune (routed experts only; shared_expert untouched)
log(f"PHASE B: prune to {KEEP_FRAC:.0%} experts/block (union+floor keep-set)")
for name, m in moe.items():
    keep = plan[name]; ex = m.experts; gt = m.gate
    assert max(keep) < n_exp and min(keep) >= 0, f"{name}: keep idx out of range"
    with torch.no_grad():
        ex.gate_up_proj = nn.Parameter(ex.gate_up_proj.data[keep].clone(), requires_grad=False)
        ex.down_proj    = nn.Parameter(ex.down_proj.data[keep].clone(), requires_grad=False)
        gt.weight       = nn.Parameter(gt.weight.data[keep].clone(), requires_grad=False)
    for obj in (ex, gt, m):
        if hasattr(obj, "num_experts"): obj.num_experts = len(keep)
    if hasattr(gt, "top_k"): gt.top_k = min(int(gt.top_k), len(keep))
    gc.collect()
    if torch.cuda.is_available(): torch.cuda.empty_cache()
keep_n = len(plan[next(iter(plan))])
for attr in ("num_experts", "num_local_experts"):
    if hasattr(model.config, attr): setattr(model.config, attr, keep_n)
n_after = sum(p.numel() for p in model.parameters())
log(f"  params {n_params0/1e9:.1f}B -> {n_after/1e9:.1f}B | experts/block {n_exp}->{keep_n}")

# quick sanity gens (EN + RU + math + char)
for probe in ["def is_prime(n):", "Столица Франции —", "17 + 25 =", "Reverse SECURITY:"]:
    with torch.no_grad():
        ids = tok(probe, return_tensors="pt").to(DEV)
        out = model.generate(**ids, max_new_tokens=20, do_sample=False)
    log(f"  GEN [{probe}] -> {tok.decode(out[0], skip_special_tokens=True)[:100]!r}")

log(f"saving pruned BF16 to {PRUNED} (~277GB, sharded)")
model.save_pretrained(PRUNED, safe_serialization=True, max_shard_size="20GB")
tok.save_pretrained(PRUNED)
json.dump({"params_before": n_params0, "params_after": n_after,
           "experts_per_block": [n_exp, keep_n], "keep_frac": KEEP_FRAC},
          open(f"{OUT}/prune_result_{VER}.json", "w"))
log("RE-PRUNE COMPLETE. Next: validate BF16 in cloud (per-capability), then heal.")
