#!/usr/bin/env python3
"""
SLOT ACCOUNTING — measure how much a topic "costs" in kept-expert slots.

Model size is FIXED by KEEP_FRAC (we always keep the top K experts/layer), so a
topic does NOT add size — it DISPLACES priority experts out of the fixed budget.
This script quantifies that displacement per candidate topic, so include/exclude
decisions are made with numbers, not vibes.

Definitions (per MoE layer, K = round(num_experts*KEEP_FRAC) kept):
  footprint(T)      = experts T routes to (top experts covering COVER=90% of T's tokens)
  keepset(corpus)   = global top-K experts by summed activation over `corpus`
  marginal cost(T)  = experts in keepset(PRIORITY + T) that are NOT in keepset(PRIORITY)
                      = how many PRIORITY experts T kicks out of the budget (displacement)
  unique(T)         = footprint(T) not already in keepset(PRIORITY)  (what keeping T needs)
  overlap(T)        = |footprint(T) ∩ keepset(PRIORITY)| / |footprint(T)|  (1.0 = free)

Run on the 397B (during Phase A — forward passes only, the cheap part) or any proxy
MoE via MODEL_ID. Reuses the same packed-expert gate hook as pipeline_397b.py.
"""
import os, json, time
import torch, torch.nn.functional as F
from collections import defaultdict
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL = os.environ.get("MODEL_ID", "Qwen/Qwen3.5-397B-A17B")
KEEP_FRAC = float(os.environ.get("KEEP_FRAC", "0.32"))
COVER = float(os.environ.get("FOOTPRINT_COVER", "0.90"))
OUT = "/workspace/run397b/slot_accounting.json"; os.makedirs(os.path.dirname(OUT), exist_ok=True)
t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:5.1f}m]", *a, flush=True)

# ---- topic-labeled calibration. PRIORITY = the domains we are specializing for. ----
# CANDIDATE = topics you are considering adding; measured against PRIORITY.
PRIORITY = {
    "websec": ["Explain and test a reflected XSS in an input field.",
               "Review an endpoint for SQL injection and SSRF.",
               "IDOR test plan for /api/orders/{id}; JWT alg:none bypass."],
    "exploit": ["Outline a PoC from a deserialization CVE.",
                "Basic stack overflow and ROP past NX/ASLR.",
                "Use nuclei templates to scan a host for known CVEs."],
    "netsec": ["ARP spoofing detection on a switched LAN; nmap SYN scan.",
               "TCP vs UDP enumeration; MITM on plaintext HTTP."],
    "debug_re": ["strace a hung PHP-FPM worker, zbacktrace to .php:line.",
                 "gdb attach, thread apply all bt, find a deadlock.",
                 "objdump -d read x86-64 ABI args; Frida hook + patch jz->nop."],
    "code": ["Implement an LRU cache; write a Go rate-limiter middleware.",
             "Debug a Postgres seq-scan; multi-stage distroless Dockerfile."],
    "agentic": ["Write a quoted heredoc to stage a script over ssh and run it.",
                "Emit a strictly valid JSON tool-call with escaped multi-line code."],
}
CANDIDATE = {
    # examples to score — edit freely
    "chemistry": ["Explain an SN2 reaction mechanism and stereochemistry.",
                  "Balance a redox equation; describe Le Chatelier's principle."],
    "physics_hw": ["Power side-channel analysis of an AES implementation.",
                   "Voltage glitching / fault injection on a microcontroller."],
    "fiction": ["Write a tense detective scene where the villain monologues.",
                "A heist thriller: the crew plans an elaborate break-in."],
}

log(f"loading {MODEL}")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(MODEL, dtype=torch.bfloat16, device_map="auto",
                                             low_cpu_mem_usage=True, trust_remote_code=True)
model.eval()
moe = {}
for name, mod in model.named_modules():
    ex = getattr(mod, "experts", None); gt = getattr(mod, "gate", None)
    if ex is not None and hasattr(ex, "gate_up_proj") and gt is not None and hasattr(gt, "weight"):
        moe[name] = mod
layer_names = list(moe)
E = moe[layer_names[0]].experts.gate_up_proj.shape[0]
top_k = int(getattr(moe[layer_names[0]].gate, "top_k", 10))
K = max(top_k, round(E * KEEP_FRAC))
# params per routed expert (gate_up + down)
gu = moe[layer_names[0]].experts.gate_up_proj
dp = moe[layer_names[0]].experts.down_proj
per_expert = gu[0].numel() + dp[0].numel()
log(f"{len(moe)} MoE layers, {E} experts/layer, top_k={top_k}, KEEP={K}/layer; per-expert={per_expert/1e6:.2f}M params (~{per_expert*0.5/1e6:.1f}MB NVFP4)")

@torch.no_grad()
def counts_for(prompts):
    c = {n: torch.zeros(E, dtype=torch.long) for n in layer_names}
    hooks = []
    def mk(n):
        def hook(m, inp, out):
            h = inp[0];  h = h.reshape(-1, h.shape[-1]) if h.dim()==3 else h
            W = m.weight
            idx = F.linear(h.to(W.dtype), W).float().topk(min(top_k, E), dim=-1).indices.reshape(-1).cpu()
            c[n] += torch.bincount(idx, minlength=E)
        return hook
    for n in layer_names: hooks.append(moe[n].gate.register_forward_hook(mk(n)))
    for p in prompts:
        ids = tok(p, return_tensors="pt", truncation=True, max_length=256).to(model.device)
        model(**ids)
    for hk in hooks: hk.remove()
    return c

def keepset(counts):  # per layer: indices of top-K experts
    return {n: set(counts[n].topk(K).indices.tolist()) for n in layer_names}
def footprint(counts):  # per layer: smallest expert set covering COVER of tokens
    fp = {}
    for n in layer_names:
        v, idx = counts[n].sort(descending=True)
        tot = max(1, int(v.sum())); cum = 0; s = set()
        for val, i in zip(v.tolist(), idx.tolist()):
            if cum >= COVER*tot: break
            s.add(i); cum += val
        fp[n] = s
    return fp

log("PRIORITY corpus pass")
prio_counts = {n: torch.zeros(E, dtype=torch.long) for n in layer_names}
for topic, prompts in PRIORITY.items():
    c = counts_for(prompts)
    for n in layer_names: prio_counts[n] += c[n]
prio_keep = keepset(prio_counts)

results = {}
for topic, prompts in CANDIDATE.items():
    log(f"scoring candidate: {topic}")
    cc = counts_for(prompts)
    fp = footprint(cc)
    combined = {n: prio_counts[n] + cc[n] for n in layer_names}
    comb_keep = keepset(combined)
    displaced = sum(len(comb_keep[n] - prio_keep[n]) for n in layer_names)   # priority experts kicked out
    unique = sum(len(fp[n] - prio_keep[n]) for n in layer_names)             # topic experts not already kept
    fp_sz = sum(len(fp[n]) for n in layer_names)
    overlap = 1.0 - (unique / max(1, fp_sz))
    results[topic] = {
        "displaced_slots": displaced,
        "displaced_pct_of_budget": round(100*displaced/(K*len(layer_names)), 2),
        "unique_experts": unique,
        "overlap_with_priority": round(overlap, 3),
        "cost_MB_nvfp4": round(displaced * per_expert * 0.5 / 1e6, 1),
        "cost_GB_bf16": round(displaced * per_expert * 2 / 1e9, 2),
    }
    log(f"  {topic}: displaces {displaced} priority experts "
        f"({results[topic]['displaced_pct_of_budget']}% of budget), "
        f"overlap={overlap:.2f}, ~{results[topic]['cost_MB_nvfp4']}MB NVFP4 of priority capacity lost")

json.dump({"model": MODEL, "experts_per_layer": E, "layers": len(layer_names), "keep_per_layer": K,
           "per_expert_params": per_expert, "results": results}, open(OUT, "w"), indent=2)
log(f"DONE -> {OUT}")
print("\nDECISION RULE: overlap>0.8 (displaced<~2% budget) => cheap, add freely; "
      "0.4-0.8 => weigh it; <0.4 (orthogonal) => heavy dilution, prefer RAG or drop.", flush=True)
