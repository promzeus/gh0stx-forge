#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_check_mtp.py — pre-deploy sanity+comparison gate for a grafted MTP head.

Extends dflash/_archive/train_mtp_distill.py's MODE=gate (that script's job was self-distill
TRAINING, archived after it regressed serve acceptance — this is a standalone comparison
tool, not a revival of that training path) with a PER-DOMAIN breakdown: top-1 accuracy
(student argmax == teacher argmax at position 0) on held-out hidden-state samples (Step B's
output, which carries `domain`), for ONE grafted checkpoint at a time.

Run it TWICE — once per MTP_MODEL_DIR (the old L2-norm graft vs the new calibrated graft,
same held-out HS_DIR both times) — and diff the two JSON outputs. Accept the new keep-set
only if it does not regress on ANY domain, not just in pooled aggregate (a code-domain loss
masked by a russian-domain gain would pass a pooled >0.5 sanity check but should not pass
this comparison).

ENV: MTP_MODEL_DIR (a graft_mtp.py output dir, KEEP=174 experts — either keep-set),
     HS_DIR (Step B's held-out samples), OUT_JSON (default ./gate_check.json),
     HELD_N (default 0 = all files in HS_DIR).
"""
import os
import json
import glob
import time

import torch
import torch.nn as nn
from safetensors import safe_open

def log(*a):
    print(f"[gate-check {(time.time() - T0) / 60:6.1f}m]", *a, flush=True)


T0 = time.time()

MTP_MODEL_DIR = os.environ["MTP_MODEL_DIR"]
HS_DIR = os.environ["HS_DIR"]
OUT_JSON = os.environ.get("OUT_JSON", "./gate_check.json")
HELD_N = int(os.environ.get("HELD_N", "0")) or None

from transformers import AutoConfig
from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe as M

hf_cfg = AutoConfig.from_pretrained(MTP_MODEL_DIR, trust_remote_code=True)
lcfg = getattr(hf_cfg, "text_config", hf_cfg)
lcfg.num_hidden_layers = 1
lcfg.layer_types = ["full_attention"]
NEXP = int(getattr(lcfg, "num_experts"))
H = lcfg.hidden_size
V = lcfg.vocab_size
EPS = lcfg.rms_norm_eps
log(f"cfg: H={H} V={V} experts={NEXP} (grafted keep-set at {MTP_MODEL_DIR})")


class MTPHead(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.embed_tokens = nn.Embedding(V, H)
        self.fc = nn.Linear(H * 2, H, bias=False)
        self.pre_fc_norm_embedding = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.pre_fc_norm_hidden = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.layer = M.Qwen3_5MoeDecoderLayer(cfg, layer_idx=0)
        self.norm = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.rotary = M.Qwen3_5MoeTextRotaryEmbedding(cfg)
        self.lm_head = nn.Linear(H, V, bias=False)

    def forward(self, hidden_in, input_ids, position_ids, attn_mask):
        e = self.pre_fc_norm_embedding(self.embed_tokens(input_ids))
        h = self.pre_fc_norm_hidden(hidden_in)
        h = self.fc(torch.cat([e, h], dim=-1))
        pos_emb = self.rotary(h, position_ids)
        out = self.layer(h, position_embeddings=pos_emb, attention_mask=attn_mask, position_ids=position_ids)
        out = out[0] if isinstance(out, tuple) else out
        return self.norm(out)


DEV = "cuda" if torch.cuda.is_available() else "cpu"
model = MTPHead(lcfg).to(DEV).to(torch.bfloat16)


def load_grafted():
    idx = None
    for f in glob.glob(os.path.join(MTP_MODEL_DIR, "*index.json")):
        idx = json.load(open(f))["weight_map"]
        break
    assert idx, f"no index.json in {MTP_MODEL_DIR}"
    need = set(
        ck for ck in idx
        if ck.startswith("mtp.") or ck == "lm_head.weight"
        or (ck.endswith("embed_tokens.weight") and "mtp" not in ck)
    )
    byshard = {}
    for ck in need:
        byshard.setdefault(idx[ck], []).append(ck)
    raw = {}
    for shard, cks in byshard.items():
        with safe_open(os.path.join(MTP_MODEL_DIR, shard), "pt") as f:
            for ck in cks:
                raw[ck] = f.get_tensor(ck).to(torch.bfloat16)
    sd = {}
    for ck, t in raw.items():
        if ck == "mtp.fc.weight":
            sd["fc.weight"] = t
        elif ck == "mtp.norm.weight":
            sd["norm.weight"] = t
        elif ck == "mtp.pre_fc_norm_embedding.weight":
            sd["pre_fc_norm_embedding.weight"] = t
        elif ck == "mtp.pre_fc_norm_hidden.weight":
            sd["pre_fc_norm_hidden.weight"] = t
        elif ck.endswith("embed_tokens.weight"):
            sd["embed_tokens.weight"] = t
        elif ck == "lm_head.weight":
            sd["lm_head.weight"] = t
        elif ck.startswith("mtp.layers.0.") and ".mlp.experts." not in ck:
            sd["layer." + ck[len("mtp.layers.0."):]] = t
    gate_up, down = [], []
    for e in range(NEXP):
        p = f"mtp.layers.0.mlp.experts.{e}."
        gate_up.append(torch.cat([raw[p + "gate_proj.weight"], raw[p + "up_proj.weight"]], 0))
        down.append(raw[p + "down_proj.weight"])
    sd["layer.mlp.experts.gate_up_proj"] = torch.stack(gate_up, 0).contiguous()
    sd["layer.mlp.experts.down_proj"] = torch.stack(down, 0).contiguous()
    return sd


sd = load_grafted()
missing, unexpected = model.load_state_dict(sd, strict=False)
missing = [m for m in missing if "inv_freq" not in m]
log(f"loaded {len(sd)} | missing {len(missing)} | unexpected {len(unexpected)}")
assert not missing, f"uncovered weights — STOP: {missing[:10]}"
model.eval()

files = sorted(glob.glob(os.path.join(HS_DIR, "*.pt")))
assert files, f"no .pt files in {HS_DIR}"
if HELD_N:
    files = files[:HELD_N]
log(f"{len(files)} held-out samples")

per_domain = {}


@torch.no_grad()
def run_one(fp):
    d = torch.load(fp, map_location="cpu")
    ids = d["input_ids"].long().to(DEV)
    domain = d["domain"]
    hs = d["hidden_states"].to(torch.bfloat16).to(DEV)
    L = min(ids.shape[0], hs.shape[0])
    if L < 4:
        return None
    hin = hs[: L - 1].unsqueeze(0)
    tin = ids[1:L].unsqueeze(0)
    pos = torch.arange(0, L - 1, device=DEV).unsqueeze(0)
    causal = torch.full((L - 1, L - 1), float("-inf"), device=DEV, dtype=hs.dtype).triu(1)
    mask = causal[None, None]
    out = model(hin, tin, pos, mask)
    student = model.lm_head(out)                    # [1, L-1, V]
    teacher = model.lm_head(hs[1:L].unsqueeze(0))    # [1, L-1, V]
    label = ids[2:L].unsqueeze(0)
    n = min(student.shape[1] - 1, label.shape[1])
    sp = student[:, :n].argmax(-1)
    tp = teacher[:, :n].argmax(-1)
    return domain, int((sp == tp).sum().item()), int(sp.numel())


for i, fp in enumerate(files):
    r = run_one(fp)
    if r is None:
        continue
    domain, corr, tot = r
    if domain not in per_domain:
        per_domain[domain] = {"corr": 0, "tot": 0}
    per_domain[domain]["corr"] += corr
    per_domain[domain]["tot"] += tot
    if (i + 1) % 100 == 0:
        log(f"  {i + 1}/{len(files)}")

result = {"mtp_model_dir": MTP_MODEL_DIR, "per_domain": {}}
overall_corr = overall_tot = 0
for d, v in sorted(per_domain.items()):
    acc = v["corr"] / max(v["tot"], 1)
    result["per_domain"][d] = {"acc": acc, "corr": v["corr"], "tot": v["tot"]}
    overall_corr += v["corr"]
    overall_tot += v["tot"]
    log(f"  [{d}] acc={acc:.4f} ({v['corr']}/{v['tot']})")
result["overall_acc"] = overall_corr / max(overall_tot, 1)
log(f"OVERALL acc={result['overall_acc']:.4f} [untrained sanity range ~0.5-0.8; below 0.5 = "
    f"convention/weights wrong]")
if result["overall_acc"] < 0.5:
    log("!!! GATE FAILED — convention/weights wrong. STOP.")
    raise SystemExit(1)

json.dump(result, open(OUT_JSON, "w"), indent=2)
log(f"-> {OUT_JSON}")
log("To compare old vs new keep-set: run this script with MTP_MODEL_DIR = old graft, then "
    "= new graft, same HS_DIR both times; diff result['per_domain'][d]['acc'] for EVERY d — "
    "accept the new keep-set only if no domain regresses.")
