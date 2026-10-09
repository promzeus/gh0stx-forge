#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibrate_mtp_keepset.py — доменная калибровка keep-set MTP-головы (512 -> KEEP экспертов).

Read BASE_DIR's mtp.* (512 experts, verbatim from Qwen3.5-397B base), the served/target
checkpoint's embed_tokens (MODEL_DIR), and Step B's per-sample hidden-state extractions
(HS_DIR: *.pt files each holding {input_ids, domain, hidden_states}, index-aligned per the
established MTP convention: hidden[j] = POST-final-norm at position j, head sees
(h_j, embed(x_{j+1}), pos=j), predicts x_{j+2}).

Counts per-domain expert-routing frequency via a REAL forward through a reconstructed
MTPHead (class lifted from pipeline/dflash/_archive/train_mtp_distill.py), one sample at a
time — no batching, no padding: batching risks mixing domains in one forward (corrupting
per-domain counts) and the causal mask lets a padded query attend into real keys (a fake
routing event gets counted as real). Neither risk exists in a single-sample loop, and this
is cheap enough (~800 short sequences, one decoder layer, forward-only) that batching buys
nothing here.

Selection: round-robin union across domains + floor, algorithm lifted verbatim from
pipeline/pipeline_reap.py (Phase A keep-plan) — single "layer" instead of a loop over many.

ENV: BASE_DIR (397B base, has mtp.* on 512 experts), MODEL_DIR (served/target checkpoint —
     source for embed_tokens, must match what produced HS_DIR), HS_DIR (Step B output),
     KEEP (default 174), MTP_LAYER (default 0), OUT_DIR (default /workspace/mtp_calib),
     FLOOR_MULT (default 3.0 — per-domain routing-event floor = FLOOR_MULT * keep_g/n_domains;
     below this, ABORT rather than trust a too-thin domain silently).
"""
import os, json, glob, time
import torch
import torch.nn as nn
import torch.nn.functional as F
from safetensors import safe_open

def log(*a):
    print(f"[calib-mtp {(time.time()-T0)/60:6.1f}m]", *a, flush=True)

T0 = time.time()

BASE_DIR   = os.environ["BASE_DIR"]
MODEL_DIR  = os.environ["MODEL_DIR"]
HS_DIR     = os.environ["HS_DIR"]
KEEP       = int(os.environ.get("KEEP", "174"))
MTP_LAYER  = int(os.environ.get("MTP_LAYER", "0"))
OUT        = os.environ.get("OUT_DIR", "/workspace/mtp_calib")
FLOOR_MULT = float(os.environ.get("FLOOR_MULT", "3.0"))
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------------------
# 1. Config — FRESH from BASE_DIR (self-consistent at 512 experts), never a
#    mutated copy of the pruned/served config (that one is self-consistent at
#    174, and hand-mutating num_experts back up risks a stale/mismatched
#    second attribute — pipeline_reap.py itself sets BOTH num_experts and
#    num_local_experts defensively, evidence this arch reads expert-count
#    from more than one place).
# ---------------------------------------------------------------------------
from transformers import AutoConfig
from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe as M

hf_cfg = AutoConfig.from_pretrained(BASE_DIR, trust_remote_code=True)
lcfg = getattr(hf_cfg, "text_config", hf_cfg)
lcfg.num_hidden_layers = 1
lcfg.layer_types = ["full_attention"]  # works around the HF layer-0 linear_attn mis-report
NEXP = int(getattr(lcfg, "num_experts"))
assert NEXP == 512, f"BASE_DIR config num_experts={NEXP}, expected 512 — wrong base checkpoint?"
H = lcfg.hidden_size
EPS = lcfg.rms_norm_eps
TOP_K_DEFAULT = int(getattr(lcfg, "num_experts_per_tok", 10))
log(f"cfg: H={H} experts={NEXP} top_k={TOP_K_DEFAULT} KEEP={KEEP}")


class MTPHead(nn.Module):
    """Standalone MTP head reconstruction — architecture lifted verbatim from
    pipeline/dflash/_archive/train_mtp_distill.py. No lm_head: not needed for a
    counting-only pass, and dropping it removes a directory-confusion source."""

    def __init__(self, cfg, vocab_size):
        super().__init__()
        self.embed_tokens = nn.Embedding(vocab_size, H)
        self.fc = nn.Linear(H * 2, H, bias=False)
        self.pre_fc_norm_embedding = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.pre_fc_norm_hidden = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.layer = M.Qwen3_5MoeDecoderLayer(cfg, layer_idx=0)
        self.norm = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.rotary = M.Qwen3_5MoeTextRotaryEmbedding(cfg)

    def forward(self, hidden_in, input_ids, position_ids, attn_mask):
        e = self.pre_fc_norm_embedding(self.embed_tokens(input_ids))
        h = self.pre_fc_norm_hidden(hidden_in)
        h = self.fc(torch.cat([e, h], dim=-1))
        pos_emb = self.rotary(h, position_ids)
        out = self.layer(h, position_embeddings=pos_emb, attention_mask=attn_mask, position_ids=position_ids)
        out = out[0] if isinstance(out, tuple) else out
        return self.norm(out)


DEV = "cuda" if torch.cuda.is_available() else "cpu"
model = MTPHead(lcfg, hf_cfg.vocab_size if hasattr(hf_cfg, "vocab_size") else lcfg.vocab_size).to(DEV).to(torch.bfloat16)

# Shape asserts BEFORE load_state_dict — a fast, clear failure instead of a
# confusing shape-mismatch traceback deep inside load_state_dict.
assert model.layer.mlp.experts.gate_up_proj.shape[0] == 512, \
    f"gate_up_proj experts={model.layer.mlp.experts.gate_up_proj.shape[0]}, expected 512"
assert model.layer.mlp.gate.weight.shape[0] == 512, \
    f"gate.weight experts={model.layer.mlp.gate.weight.shape[0]}, expected 512"
log("shape-asserts OK (512 experts)")


# ---------------------------------------------------------------------------
# 2. Load weights: mtp.* experts/gate/fc/norms from BASE_DIR (all 512),
#    embed_tokens from MODEL_DIR (the served/target checkpoint — same source
#    that produced HS_DIR's hidden-states). lm_head is not loaded at all.
# ---------------------------------------------------------------------------
def read_tensors(model_dir, keys):
    idx = None
    for f in glob.glob(os.path.join(model_dir, "*index.json")):
        idx = json.load(open(f))["weight_map"]
        break
    assert idx is not None, f"no *index.json in {model_dir}"
    byshard = {}
    for k in keys:
        assert k in idx, f"{k} missing from index in {model_dir}"
        byshard.setdefault(idx[k], []).append(k)
    out = {}
    for shard, ks in byshard.items():
        with safe_open(os.path.join(model_dir, shard), "pt") as f:
            for k in ks:
                out[k] = f.get_tensor(k)
    return out


base_idx = None
for f in glob.glob(os.path.join(BASE_DIR, "*index.json")):
    base_idx = json.load(open(f))["weight_map"]
    break
assert base_idx is not None, f"no *index.json in {BASE_DIR}"
base_mtp_keys = [
    k for k in base_idx
    if k.startswith(f"mtp.layers.{MTP_LAYER}.")
    or k in ("mtp.fc.weight", "mtp.norm.weight",
             "mtp.pre_fc_norm_embedding.weight", "mtp.pre_fc_norm_hidden.weight")
]
assert base_mtp_keys, f"no mtp.* keys in {BASE_DIR} — wrong base checkpoint?"
raw = read_tensors(BASE_DIR, base_mtp_keys)
log(f"read {len(raw)} mtp.* tensors from BASE_DIR")

model_idx = None
for f in glob.glob(os.path.join(MODEL_DIR, "*index.json")):
    model_idx = json.load(open(f))["weight_map"]
    break
assert model_idx is not None, f"no *index.json in {MODEL_DIR}"
embed_key = next(k for k in model_idx if k.endswith("embed_tokens.weight") and "mtp" not in k)
embed_raw = read_tensors(MODEL_DIR, [embed_key])
log(f"embed_tokens.weight <- MODEL_DIR ({embed_key})")

sd = {}
for k, v in raw.items():
    v = v.to(torch.bfloat16)
    if k == "mtp.fc.weight":
        sd["fc.weight"] = v
    elif k == "mtp.norm.weight":
        sd["norm.weight"] = v
    elif k == "mtp.pre_fc_norm_embedding.weight":
        sd["pre_fc_norm_embedding.weight"] = v
    elif k == "mtp.pre_fc_norm_hidden.weight":
        sd["pre_fc_norm_hidden.weight"] = v
    elif k.startswith(f"mtp.layers.{MTP_LAYER}.") and ".mlp.experts." not in k:
        sd["layer." + k[len(f"mtp.layers.{MTP_LAYER}."):]] = v
sd["embed_tokens.weight"] = embed_raw[embed_key].to(torch.bfloat16)

gate_up, down = [], []
for e in range(NEXP):
    p = f"mtp.layers.{MTP_LAYER}.mlp.experts.{e}."
    gate_up.append(torch.cat([raw[p + "gate_proj.weight"], raw[p + "up_proj.weight"]], 0).to(torch.bfloat16))
    down.append(raw[p + "down_proj.weight"].to(torch.bfloat16))
sd["layer.mlp.experts.gate_up_proj"] = torch.stack(gate_up, 0).contiguous()
sd["layer.mlp.experts.down_proj"] = torch.stack(down, 0).contiguous()

missing, unexpected = model.load_state_dict(sd, strict=False)
missing = [m for m in missing if "inv_freq" not in m]
log(f"loaded {len(sd)} | missing {len(missing)} | unexpected {len(unexpected)}")
if unexpected:
    log(f"  UNEXPECTED: {unexpected[:6]}")
assert not missing, f"uncovered weights (convention drift?) — STOP: {missing[:10]}"
model.eval()

# ---------------------------------------------------------------------------
# 3. Per-domain routing counts — ONE SAMPLE AT A TIME, no batching, no padding.
# ---------------------------------------------------------------------------
files = sorted(glob.glob(os.path.join(HS_DIR, "*.pt")))
assert files, f"no .pt files in {HS_DIR} — has Step B (hidden-state extraction) run yet?"
log(f"found {len(files)} hidden-state samples")

probe_domains = set()
for f in files[: min(50, len(files))]:
    probe_domains.add(torch.load(f, map_location="cpu")["domain"])
DOMAINS = sorted(probe_domains) if len(files) <= 50 else None  # finalize after full scan below

counts = {}
n_events = {}


def get_counters(d):
    if d not in counts:
        counts[d] = torch.zeros(NEXP, dtype=torch.long)
        n_events[d] = 0
    return counts[d]


cap = {"d": None}


def gate_hook(mod, inp, out):
    h = inp[0]
    if h.dim() == 3:
        h = h.reshape(-1, h.shape[-1])
    logits = F.linear(h.float(), mod.weight.float())
    k = min(int(getattr(mod, "top_k", TOP_K_DEFAULT)), logits.shape[-1])
    idx = logits.topk(k, dim=-1).indices.reshape(-1).cpu()
    c = get_counters(cap["d"])
    c += torch.bincount(idx, minlength=NEXP)
    n_events[cap["d"]] += idx.numel()


handle = model.layer.mlp.gate.register_forward_hook(gate_hook)

with torch.no_grad():
    for i, fp in enumerate(files):
        d = torch.load(fp, map_location="cpu")
        cap["d"] = d["domain"]
        ids = d["input_ids"].long().to(DEV)
        hs = d["hidden_states"].to(torch.bfloat16).to(DEV)
        L = min(ids.shape[0], hs.shape[0])
        if L < 4:
            continue
        # MTP convention: at buffer index j, head sees (h_j, embed(x_{j+1}), pos=j).
        # No padding here — this is a single real sequence, length exactly L-1.
        hin = hs[: L - 1].unsqueeze(0)
        tin = ids[1:L].unsqueeze(0)
        pos = torch.arange(0, L - 1, device=DEV).unsqueeze(0)
        causal = torch.full((L - 1, L - 1), float("-inf"), device=DEV, dtype=hs.dtype).triu(1)
        mask = causal[None, None]
        model(hin, tin, pos, mask)
        if (i + 1) % 100 == 0:
            log(f"  {i + 1}/{len(files)} samples processed")
handle.remove()

DOMAINS = sorted(counts.keys())
log(f"domains observed: {DOMAINS}")

# ---------------------------------------------------------------------------
# 4. Per-domain numeric floor — NOT just assert tot > 0. A thin domain (e.g.
#    calib_corpus.py's offline 'precision' caps at 12 sequences) can pass a
#    bare nonzero check while being statistically meaningless for a SINGLE
#    non-redundant gate (unlike the main model's 40+ layers, where thinness
#    averages out across depth).
# ---------------------------------------------------------------------------
keep_g = KEEP
floor = FLOOR_MULT * (keep_g / len(DOMAINS))
for d in DOMAINS:
    log(f"  domain '{d}': events={n_events[d]} (floor={floor:.0f})")
    assert n_events[d] > floor, (
        f"domain '{d}' has only {n_events[d]} routing events (floor {floor:.0f}) — "
        f"calibration too thin for this domain, STOP. Increase target_total in "
        f"calib_corpus.assemble(), or add a local pool for '{d}' (mirroring the "
        f"corpus_offsec_clean.json fix already done for offsec) before trusting this keep-set."
    )

# ---------------------------------------------------------------------------
# 5. Selection — round-robin union across domains + floor, algorithm lifted
#    verbatim from pipeline/pipeline_reap.py's Phase-A keep-plan (single
#    "layer" instead of a loop over many).
# ---------------------------------------------------------------------------
ranked = {d: counts[d].argsort(descending=True).tolist() for d in DOMAINS}
keep, ptrs = set(), {d: 0 for d in DOMAINS}
while len(keep) < keep_g:
    progressed = False
    for d in DOMAINS:
        while ptrs[d] < NEXP:
            e = ranked[d][ptrs[d]]
            ptrs[d] += 1
            if counts[d][e] > 0 and e not in keep:
                keep.add(e)
                progressed = True
                break
        if len(keep) >= keep_g:
            break
    if not progressed:
        break
if len(keep) < keep_g:
    tot = sum(counts[d] for d in DOMAINS)
    for e in tot.argsort(descending=True).tolist():
        if e not in keep:
            keep.add(e)
        if len(keep) >= keep_g:
            break
keep = sorted(keep)
log(f"keep-set: {len(keep)} experts (first 10: {keep[:10]})")

domain_stats = {}
for d in DOMAINS:
    distinct = int((counts[d] > 0).sum())
    kept_hit = int(sum(1 for e in keep if counts[d][e] > 0))
    domain_stats[d] = {"total_events": n_events[d], "distinct_experts_hit": distinct, "kept_experts_hit": kept_hit}
    log(f"  [{d}] events={n_events[d]} distinct={distinct} kept_hit={kept_hit}/{len(keep)}")

out_path = os.path.join(OUT, "mtp_keep_idx.json")
json.dump(
    {
        "keep_idx": keep,
        "keep_frac": KEEP / NEXP,
        "domains": DOMAINS,
        "counts": {d: counts[d].tolist() for d in DOMAINS},
        "n_events": n_events,
        "domain_stats": domain_stats,
        "mtp_layer": MTP_LAYER,
        "base_dir": BASE_DIR,
        "model_dir": MODEL_DIR,
        "floor_mult": FLOOR_MULT,
    },
    open(out_path, "w"),
    indent=2,
)
log(f"DONE in {(time.time() - T0) / 60:.1f}m -> {out_path}")
