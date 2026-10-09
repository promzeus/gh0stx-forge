#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
graft_mtp.py — вшить MTP-голову (spec-decode) в NVFP4-чекпоинт обрезанной Qwen3.5-MoE.

Читает mtp.* из базы 397B (BASE, на PVC), режет её 512 экспертов → KEEP (=config.num_experts обрезанной
модели) по router-weight-norm (L2 строк mtp.layers.0.mlp.gate.weight), перенумеровывает 0..KEEP-1,
пишет ОДИН новый bf16-шард + патчит index.json (total_size += delta, без чтения всех шардов) + config.json
(mtp_num_hidden_layers=1, ignore += re:.*mtp.*). Overlay: НЕ требует наличия 88G-шардов target локально —
нужны только его config.json + index.json (кладём в QUANT_META). Выход (shard+index+config) → OUT.

Рецепт: research 2026-07-04 (lna-lab/GGUF-to-NVFP4-SM120 + vLLM qwen3_5_mtp.py). Ключи ДОЛЖНЫ быть mtp.*
(vLLM ремапит mtp.→model.). fc/нормы/эксперты bf16 (в ignore → vLLM строит unquantized MoE для MTP-слоя).
"""
import json, re, os, sys
from pathlib import Path
import torch
from safetensors import safe_open
from safetensors.torch import save_file

BASE       = Path(os.environ["BASE_DIR"])        # база 397B bf16 (есть mtp.*), на PVC
QUANT_META = Path(os.environ["QUANT_META_DIR"])  # dir с config.json + model.safetensors.index.json обрезанной NVFP4
OUT        = Path(os.environ["OUT_DIR"]); OUT.mkdir(parents=True, exist_ok=True)
MTP_LAYER  = int(os.environ.get("MTP_LAYER", "0"))
NEW_SHARD  = "model-mtp-grafted.safetensors"

def log(m): print(f"[graft_mtp] {m}", flush=True)

# KEEP = config.num_experts обрезанной модели
cfg = json.loads((QUANT_META/"config.json").read_text())
tcfg = cfg.get("text_config", cfg)
KEEP = int(tcfg["num_experts"])
log(f"target num_experts (KEEP) = {KEEP}")

# 1. читаем mtp.* из базы через index (safe_open, только нужные шарды)
bidx = json.loads((BASE/"model.safetensors.index.json").read_text())["weight_map"]
mtp_keys = [k for k in bidx if k.startswith("mtp.")]
assert mtp_keys, "в базе нет mtp.* — не та база?"
shard_of = {}
for k in mtp_keys: shard_of.setdefault(bidx[k], []).append(k)
mtp = {}
for shard, keys in shard_of.items():
    with safe_open(str(BASE/shard), framework="pt") as f:
        for k in keys: mtp[k] = f.get_tensor(k)
log(f"прочитано {len(mtp)} mtp.* тензоров из {len(shard_of)} шардов базы")

# 2. keep-set: калиброванный (MTP_KEEP_IDX_JSON, см. calibrate_mtp_keepset.py) если задан,
#    иначе fallback на старый router-weight L2-norm shortcut (без калибровки) — сохранён
#    ради Step D (сравнение старого/нового keep-set требует обоих путей).
gate_key = f"mtp.layers.{MTP_LAYER}.mlp.gate.weight"
gate = mtp[gate_key].float()   # [n_exp, hidden]
n_exp = gate.shape[0]
assert n_exp >= KEEP, f"gate {gate.shape} < KEEP {KEEP}"
KEEP_IDX_JSON = os.environ.get("MTP_KEEP_IDX_JSON", "")
if KEEP_IDX_JSON:
    calib = json.loads(Path(KEEP_IDX_JSON).read_text())
    kept = sorted(calib["keep_idx"])
    assert len(kept) == KEEP, f"mtp_keep_idx.json даёт {len(kept)} экспертов, ожидали KEEP={KEEP}"
    assert all(0 <= e < n_exp for e in kept), f"keep_idx вне диапазона [0,{n_exp})"
    log(f"MTP: {n_exp} эксп → keep {KEEP} по ДОМЕННОЙ КАЛИБРОВКЕ ({KEEP_IDX_JSON}). first10={kept[:10]}")
else:
    kept = torch.topk(gate.norm(dim=1), KEEP).indices.sort().values.tolist()
    log(f"MTP: {n_exp} эксп → keep {KEEP} по router-norm (fallback, MTP_KEEP_IDX_JSON не задан). first10={kept[:10]}")
remap = {old: new for new, old in enumerate(kept)}

# 3. pruned+renumbered dict
exp_re = re.compile(rf"mtp\.layers\.{MTP_LAYER}\.mlp\.experts\.(\d+)\.(.+)")
out = {}; n_exp_t = 0
for k, v in mtp.items():
    m = exp_re.match(k)
    if m:
        old = int(m.group(1))
        if old in remap:
            out[f"mtp.layers.{MTP_LAYER}.mlp.experts.{remap[old]}.{m.group(2)}"] = v.to(torch.bfloat16).contiguous(); n_exp_t += 1
    elif k == gate_key:
        out[k] = gate[kept].to(torch.bfloat16).contiguous()
    elif k.startswith(f"mtp.layers.{MTP_LAYER}.mlp.gate."):
        out[k] = (v[kept] if v.dim() >= 1 and v.shape[0] == n_exp else v).to(torch.bfloat16).contiguous()
    else:  # fc, нормы, self_attn, shared_expert — verbatim
        out[k] = v.to(torch.bfloat16).contiguous()
log(f"graft: {len(out)} тензоров ({n_exp_t} expert-тензоров = {n_exp_t//3} эксп ×3)")

# 4. пишем новый шард
save_file(out, str(OUT/NEW_SHARD), metadata={"format": "pt"})
delta = sum(t.numel()*t.element_size() for t in out.values())
log(f"записан {NEW_SHARD}: {delta//1024//1024} МБ")

# 5. index.json: добавить ключи + total_size += delta (без чтения всех шардов)
qidx = json.loads((QUANT_META/"model.safetensors.index.json").read_text())
for k in out: qidx["weight_map"][k] = NEW_SHARD
qidx.setdefault("metadata", {})
qidx["metadata"]["total_size"] = int(qidx["metadata"].get("total_size", 0)) + delta
(OUT/"model.safetensors.index.json").write_text(json.dumps(qidx, indent=2))
log("index.json обновлён (+mtp ключи, total_size += delta)")

# 6. config.json: mtp_num_hidden_layers=1, ignore += re:.*mtp.*
tcfg["mtp_num_hidden_layers"] = 1
qc = cfg.setdefault("quantization_config", {})
ig = qc.setdefault("ignore", [])
if "re:.*mtp.*" not in ig: ig.append("re:.*mtp.*")
(OUT/"config.json").write_text(json.dumps(cfg, indent=2))
log("config.json: mtp_num_hidden_layers=1, ignore += re:.*mtp.*")
log(f"ГОТОВО. overlay в {OUT}: {NEW_SHARD} + index.json + config.json → залить поверх nvfp4_final_v2")
