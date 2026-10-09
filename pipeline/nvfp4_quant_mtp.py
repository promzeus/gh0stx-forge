#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nvfp4_quant_mtp.py — Step F: NVFP4-квантование калиброванной MTP-головы.

ПОЧЕМУ ТАК (не nvfp4_quant.py на полном чекпоинте):
llm-compressor's oneshot() калибрует ЧЕРЕЗ ФОРВАРД реальной модели, а
MTP-голова НЕ вызывается стандартным AutoModelForCausalLM.forward() — это
отдельный vLLM-специфичный спекулятивный модуль, свой forward-граф
(hidden_in, input_ids, position_ids, attn_mask), см. gate_check_mtp.py.
Значит грузить весь merged-чекпоинт и звать oneshot() на нём — бесполезно,
калибровочные данные никогда не дойдут до mtp.*-слоёв.

Решение (подтверждено исследованием исходников llm-compressor/compressed-
tensors): работаем ТОЛЬКО со standalone MTPHead-модулем (та же
реконструкция, что в gate_check_mtp.py/calibrate_mtp_keepset.py — 174
экспертов, уже прошедших доменную калибровку keep-set). Кормим его РЕАЛЬНЫМИ
сохранёнными hidden-states-семплами (Step B, mtp_hs_calib/) через
oneshot(..., pipeline="basic") — этот pipeline зовёт model(**batch) без
HF-специфичных допущений (проверено в исходниках), т.к. дефолтный
"sequential" pipeline полагается на HF-трейсинг input_ids/attention_mask,
которого у MTPHead нет.

БЕЗОПАСНОСТЬ ПО КОНСТРУКЦИИ, не runtime-проверкой: основной ствол (174-
эксперт NVFP4 trunk) в этот скрипт вообще не загружается — MTPHead
реконструируется из mtp.*-тензоров калиброванного оверлея (BF16) плюс
embed_tokens/lm_head (нужны только для forward, не сохраняются). Испортить
то, что не грузилось, невозможно.

ALLOW-LIST targets (относительно ключей MTPHead, префикс "layer." = mtp.layers.0.):
    layer.mlp.experts.gate_up_proj                  fused-stack, 174 экспертов
    layer.mlp.experts.down_proj                      fused-stack
    layer.mlp.shared_expert.{gate,up,down}_proj      ОСОЗНАННО квантуем (см. план
                                                      Step F: основной ствол квантует
                                                      свой shared_expert, делаем то же)
    layer.self_attn.{q,k,v,o}_proj
НЕ в targets (= остаётся BF16, физически не может быть тронуто): fc, все
нормы (input_layernorm/post_attention_layernorm/q_norm/k_norm/pre_fc_norm_*/
norm), mlp.gate (роутер — на нём calibrate_mtp_keepset.py's keep-set,
трогать нельзя), shared_expert_gate, embed_tokens, lm_head.
"""
from __future__ import annotations

import glob
import json
import os
import time

import torch
import torch.nn as nn
from safetensors import safe_open
from safetensors.torch import save_file

T0 = time.time()


def log(*a):
    print(f"[nvfp4_quant_mtp {(time.time() - T0) / 60:6.1f}m]", *a, flush=True)


MTP_MODEL_DIR = os.environ["MTP_MODEL_DIR"]     # калиброванный graft (174 экспертов, BF16 mtp.*)
HS_DIR = os.environ["HS_DIR"]                    # Step B held-out hidden-states (для калибровки)
OUT_DIR = os.environ.get("OUT_DIR", "./mtp_nvfp4_out")
NUM_CALIB = int(os.environ.get("NVFP4_MTP_SAMPLES", "128"))

os.makedirs(OUT_DIR, exist_ok=True)

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
log(f"cfg: H={H} V={V} experts={NEXP} (калиброванный keep-set, {MTP_MODEL_DIR})")


class _DummyHFConfig:
    """oneshot()'s pre_process() reads model.config._name_or_path -- MTPHead
    is not a PreTrainedModel and has no real HF config; this satisfies just
    that one attribute access, nothing else reads from it."""
    _name_or_path = "mtp-head-standalone"


class MTPHead(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.config = _DummyHFConfig()
        self.embed_tokens = nn.Embedding(V, H)
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
        return out[0] if isinstance(out, tuple) else out

    # oneshot()'s pre_process() unconditionally touches model.save_pretrained
    # (modify_save_pretrained wrapper) — dummy stub, we save manually below.
    def save_pretrained(self, *a, **k):
        pass


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
        if ck.startswith("mtp.") or (ck.endswith("embed_tokens.weight") and "mtp" not in ck)
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

# ---------------------------------------------------------------------------
# Калибровочный DataLoader из РЕАЛЬНЫХ сохранённых hidden-states (Step B)
# ---------------------------------------------------------------------------
files = sorted(glob.glob(os.path.join(HS_DIR, "*.pt")))[:NUM_CALIB]
assert files, f"no .pt files in {HS_DIR}"
log(f"{len(files)} калибровочных семплов из {HS_DIR}")


def make_batch(fp):
    d = torch.load(fp, map_location="cpu")
    ids = d["input_ids"].long()
    hs = d["hidden_states"].to(torch.bfloat16)
    L = min(ids.shape[0], hs.shape[0])
    hin = hs[: L - 1].unsqueeze(0).to(DEV)
    tin = ids[1:L].unsqueeze(0).to(DEV)
    pos = torch.arange(0, L - 1, device=DEV).unsqueeze(0)
    causal = torch.full((L - 1, L - 1), float("-inf"), device=DEV, dtype=hs.dtype).triu(1)
    mask = causal[None, None]
    return {"hidden_in": hin, "input_ids": tin, "position_ids": pos, "attn_mask": mask}


class CalibDataset(torch.utils.data.Dataset):
    def __len__(self):
        return len(files)

    def __getitem__(self, i):
        return make_batch(files[i])


calib_loader = torch.utils.data.DataLoader(CalibDataset(), batch_size=1, collate_fn=lambda b: b[0])

# ---------------------------------------------------------------------------
# ALLOW-LIST quantization recipe — то же API, что уже проверенно квантует
# ИДЕНТИЧНЫЙ MoE-класс (M.Qwen3_5MoeDecoderLayer) в основном стволе
# (nvfp4_quant.py), просто через кастомный pipeline="basic" вместо
# HF-токенизированного sequential-пайплайна.
# ---------------------------------------------------------------------------
MTP_TARGETS = [
    # llmcompressor "линеаризует" fused experts.gate_up_proj/down_proj в
    # individual per-expert Linear-модули ПЕРЕД калибровкой (лог: "Linearizing
    # experts... 2D -> 3D -> 2D") — итоговое имя experts.{e}.{gate,up,down}_proj,
    # НЕ fused-имя. Подтверждено эмпирически (первый прогон смотрел state_dict).
    "re:^layer\\.mlp\\.experts\\.\\d+\\.(gate_proj|up_proj|down_proj)$",
    "re:^layer\\.mlp\\.shared_expert\\.(gate_proj|up_proj|down_proj)$",
    "re:^layer\\.self_attn\\.[qkvo]_proj$",
]
log(f"ALLOW-LIST targets: {MTP_TARGETS}")

from llmcompressor import oneshot
from llmcompressor.modifiers.quantization import QuantizationModifier

recipe = QuantizationModifier(targets=MTP_TARGETS, scheme="NVFP4")

log("running oneshot NVFP4 calibration (pipeline=basic, custom MTPHead + real hidden-states) ...")
oneshot(
    model=model,
    dataset=calib_loader,
    recipe=recipe,
    pipeline="basic",
    moe_calibrate_all_experts=True,
    processor=False,   # MTPHead не HF-модель, свои pre-computed tensors -> нет процессора/токенизации
)

log("calibration done. упаковываю fake-quantized веса в финальный NVFP4 (compress_module по каждому targeted модулю) ...")
import re as _re
from compressed_tensors.compressors import compress_module

target_patterns = [_re.compile(t[len("re:"):]) for t in MTP_TARGETS]

n_compressed = 0
for name, module in model.named_modules():
    if any(p.match(name) for p in target_patterns):
        compress_module(module)
        n_compressed += 1
log(f"  {n_compressed} модулей упаковано (ожидалось 174*3 + 3 + 4 = {174*3 + 3 + 4})")

sd_out = model.state_dict()

# --- конвертация имён обратно в mtp.* конвенцию + сборка config.json ---
def to_mtp_name(k: str) -> str:
    if k in ("fc.weight",):
        return "mtp.fc.weight"
    if k == "norm.weight":
        return "mtp.norm.weight"
    if k in ("pre_fc_norm_embedding.weight", "pre_fc_norm_hidden.weight"):
        return "mtp." + k
    if k == "embed_tokens.weight":
        return None  # не сохраняем — это не mtp-специфичный тензор, берётся из основного чекпоинта
    if k.startswith("layer."):
        return "mtp.layers.0." + k[len("layer."):]
    return None


mtp_sd = {}
for k, v in sd_out.items():
    mk = to_mtp_name(k)
    if mk is not None:
        mtp_sd[mk] = v.contiguous()

log(f"  {len(mtp_sd)} тензоров в итоговом mtp.*-неймспейсе")

os.makedirs(OUT_DIR, exist_ok=True)
NEW_SHARD = "model-mtp-quantized.safetensors"
save_file(mtp_sd, os.path.join(OUT_DIR, NEW_SHARD), metadata={"format": "pt"})
log(f"-> {OUT_DIR}/{NEW_SHARD} ({len(mtp_sd)} тензоров)")

# quantization_config.ignore для НОВОГО overlay: всё, что НЕ попало в targets,
# должно остаться в ignore (fc, нормы, mlp.gate, shared_expert_gate) — targets
# сами (experts.*, shared_expert.*, self_attn.[qkvo]_proj) теперь квантованы,
# их НЕЛЬЗЯ включать в ignore.
mtp_ignore_patterns = [
    "re:^mtp\\.fc",
    "re:^mtp\\.norm",
    "re:^mtp\\.pre_fc_norm",
    "re:.*mtp.*input_layernorm",
    "re:.*mtp.*post_attention_layernorm",
    "re:.*mtp.*q_norm",
    "re:.*mtp.*k_norm",
    "re:^mtp\\.layers\\.0\\.mlp\\.gate$",
    "re:^mtp\\.layers\\.0\\.mlp\\.shared_expert_gate$",
]
meta = {
    "n_compressed_modules": n_compressed,
    "mtp_ignore_patterns_needed": mtp_ignore_patterns,
    "targets_used": MTP_TARGETS,
    "note": "Слить mtp_sd в основной release: заменить model-mtp-grafted.safetensors "
            "этим шардом, обновить index.json (те же ключи, тот же shard-файл под новым "
            "именем), и в config.json quantization_config.ignore заменить широкий "
            "'re:.*mtp.*' на mtp_ignore_patterns выше (иначе vLLM продолжит трактовать "
            "квантованные mtp-тензоры как BF16-игнорируемые).",
}
json.dump(meta, open(os.path.join(OUT_DIR, "mtp_quant_meta.json"), "w"), indent=2)
log(f"-> {OUT_DIR}/mtp_quant_meta.json (инструкция для слияния с релизом)")
log("ГОТОВО.")
