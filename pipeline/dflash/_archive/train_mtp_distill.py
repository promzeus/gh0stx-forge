#!/usr/bin/env python3
"""
train_mtp_distill.py — self-distill грефтнутой Qwen3.5 MTP-головы gh0stx на GB10 (БАТЧЕВАЯ версия).

Конвенция (verified, MTP-DISTILL-PLAN.md): hidden = POST-final-norm таргета. Индекс j -> голова видит
(h_j, embed(x_{j+1}), pos=j), предсказывает x_{j+2}. input_ids сдвиг +1, hidden/pos без сдвига.

СКОРОСТЬ: eager MoE = Python-цикл по 174 экспертам (overhead на вызов, не на токен) -> БАТЧИМ B сэмплов
(pad + causal/padding-маска) -> B×S токенов за те же 174 итерации -> ~B× быстрее.

MODE=gate|train. ENV: MODEL_DIR,HS_DIR,OUT_SAFETENSORS,EPOCHS,LR,BATCH,ACCUM,GATE_N,KD_T,HARD_W,MAX_TRAIN_LEN,TOPK_KD
"""
import os, glob, json, math, random
import torch, torch.nn as nn, torch.nn.functional as F
from safetensors import safe_open
from safetensors.torch import save_file

def log(m): print(f"[mtp-distill] {m}", flush=True)

MODEL_DIR = os.environ.get("MODEL_DIR", "/model")
HS_DIR    = os.environ.get("HS_DIR", "/hs")
OUT_ST    = os.environ.get("OUT_SAFETENSORS", "/out/model-mtp-distilled.safetensors")
MODE      = os.environ.get("MODE", "gate")
EPOCHS    = int(os.environ.get("EPOCHS", "2"))
LR        = float(os.environ.get("LR", "1e-4"))
BATCH     = int(os.environ.get("BATCH", "8"))
ACCUM     = int(os.environ.get("ACCUM", "2"))
GATE_N    = int(os.environ.get("GATE_N", "128"))
KD_T      = float(os.environ.get("KD_T", "1.0"))
HARD_W    = float(os.environ.get("HARD_W", "0.1"))
MAXLEN    = int(os.environ.get("MAX_TRAIN_LEN", "2048"))
TOPK_KD   = int(os.environ.get("TOPK_KD", "0"))   # 0=full-vocab KL; >0 = top-k KL (быстрее/легче)
PAD_TO    = int(os.environ.get("PAD_TO", "0"))    # >0 = фикс-длина всех батчей (для torch.compile)
COMPILE   = int(os.environ.get("COMPILE", "0"))   # 1 = torch.compile модели (фьюзит MoE-цикл)
DEV = "cuda"

cfg_all = json.load(open(os.path.join(MODEL_DIR, "config.json")))
from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe as M
from transformers import AutoConfig
hf_cfg = AutoConfig.from_pretrained(MODEL_DIR, trust_remote_code=True)
lcfg = getattr(hf_cfg, "text_config", hf_cfg)
lcfg.num_hidden_layers = 1
lcfg.layer_types = ["full_attention"]
NEXP = int(getattr(lcfg, "num_experts")); MOE_I = int(getattr(lcfg, "moe_intermediate_size"))
H = lcfg.hidden_size; V = lcfg.vocab_size; EPS = lcfg.rms_norm_eps
log(f"cfg: H={H} V={V} experts={NEXP} top={getattr(lcfg,'num_experts_per_tok',None)} | BATCH={BATCH} TOPK_KD={TOPK_KD}")

class MTPHead(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.embed_tokens = nn.Embedding(V, H)
        self.fc = nn.Linear(H * 2, H, bias=False)
        self.pre_fc_norm_embedding = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.pre_fc_norm_hidden    = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
        self.layer = M.Qwen3_5MoeDecoderLayer(cfg, layer_idx=0)
        self.norm  = M.Qwen3_5MoeRMSNorm(H, eps=EPS)
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

model = MTPHead(lcfg).to(DEV).to(torch.bfloat16)

def load_grafted():
    idx = None
    for f in glob.glob(os.path.join(MODEL_DIR, "*index.json")):
        idx = json.load(open(f))["weight_map"]; break
    assert idx
    need = set(ck for ck in idx if ck.startswith("mtp.") or ck == "lm_head.weight"
               or (ck.endswith("embed_tokens.weight") and "mtp" not in ck))
    byshard = {}
    for ck in need: byshard.setdefault(idx[ck], []).append(ck)
    raw = {}
    for shard, cks in byshard.items():
        with safe_open(os.path.join(MODEL_DIR, shard), "pt") as f:
            for ck in cks: raw[ck] = f.get_tensor(ck).to(torch.bfloat16)
    sd = {}
    for ck, t in raw.items():
        if ck == "mtp.fc.weight": sd["fc.weight"] = t
        elif ck == "mtp.norm.weight": sd["norm.weight"] = t
        elif ck == "mtp.pre_fc_norm_embedding.weight": sd["pre_fc_norm_embedding.weight"] = t
        elif ck == "mtp.pre_fc_norm_hidden.weight": sd["pre_fc_norm_hidden.weight"] = t
        elif ck.endswith("embed_tokens.weight"): sd["embed_tokens.weight"] = t
        elif ck == "lm_head.weight": sd["lm_head.weight"] = t
        elif ck.startswith("mtp.layers.0.") and ".mlp.experts." not in ck:
            sd["layer." + ck[len("mtp.layers.0."):]] = t
    gate_up, down = [], []
    for e in range(NEXP):
        p = f"mtp.layers.0.mlp.experts.{e}."
        gate_up.append(torch.cat([raw[p+"gate_proj.weight"], raw[p+"up_proj.weight"]], 0))
        down.append(raw[p+"down_proj.weight"])
    sd["layer.mlp.experts.gate_up_proj"] = torch.stack(gate_up, 0).contiguous()
    sd["layer.mlp.experts.down_proj"]    = torch.stack(down, 0).contiguous()
    return sd

sd = load_grafted()
missing, unexpected = model.load_state_dict(sd, strict=False)
missing = [m for m in missing if "inv_freq" not in m]
log(f"загружено {len(sd)} | missing {len(missing)} | unexpected {len(unexpected)}")
if missing: log(f"  MISSING: {missing[:6]}")
if unexpected: log(f"  UNEXPECTED: {unexpected[:6]}")
for p in model.embed_tokens.parameters(): p.requires_grad_(False)
for p in model.lm_head.parameters(): p.requires_grad_(False)
if COMPILE:
    log("torch.compile модели (dynamic=False)...")
    model = torch.compile(model, dynamic=False)

files = sorted(glob.glob(os.path.join(HS_DIR, "*.pt")))
assert files
random.seed(0); random.shuffle(files)
held = files[:GATE_N]; train_files = files[GATE_N:]
log(f"данных {len(files)} (held {len(held)}, train {len(train_files)})")

def load_pt(fp):
    d = torch.load(fp, map_location="cpu")
    ids = d["input_ids"].long(); lm = d["loss_mask"].long(); H_ = d["hidden_states"].to(torch.bfloat16)
    if ids.shape[0] > MAXLEN: ids, lm, H_ = ids[:MAXLEN], lm[:MAXLEN], H_[:MAXLEN]
    return ids, lm, H_

def make_batch(fps):
    """Пакет: pad вправо до Lmax. Возвращает hin[B,L-1,H], tin[B,L-1], pos[B,L-1], mask[B,1,L-1,L-1],
    label[B,L-2], w[B,L-2], teacher-hidden htar[B,L-2? -> используем H_[1:] ]. Все сдвиги внутри."""
    items = [load_pt(f) for f in fps]
    items = [it for it in items if it[0].shape[0] >= 4]
    if not items: return None
    L = PAD_TO if PAD_TO > 0 else max(it[0].shape[0] for it in items)
    items = [(a[:L], b[:L], c[:L]) for (a, b, c) in items]   # обрезка под фикс-длину
    B = len(items)
    ids = torch.zeros(B, L, dtype=torch.long); lm = torch.zeros(B, L, dtype=torch.long)
    Hb = torch.zeros(B, L, H, dtype=torch.bfloat16); real = torch.zeros(B, L, dtype=torch.bool)
    for i,(a,b,c) in enumerate(items):
        s = a.shape[0]; ids[i,:s]=a; lm[i,:s]=b; Hb[i,:s]=c; real[i,:s]=True
    return ids.to(DEV), lm.to(DEV), Hb.to(DEV), real.to(DEV), L

def run_batch(ids, lm, Hb, real, L):
    """Единый forward+логиты. Возвращает student[B,L-1,V], teacher[B,L-1,V], label[B,L-1], w[B,L-1]."""
    B = ids.shape[0]
    hin = Hb[:, :L-1]; tin = ids[:, 1:L]
    pos = torch.arange(0, L-1, device=DEV)[None].expand(B, -1)
    # маска: causal + key-padding (padded ключи невидимы)
    causal = torch.full((L-1, L-1), float("-inf"), device=DEV, dtype=Hb.dtype).triu(1)
    keypad = (~real[:, 1:L])[:, None, None, :].to(Hb.dtype) * float("-inf")   # [B,1,1,L-1]
    keypad = torch.nan_to_num(keypad, nan=0.0)                                # 0*(-inf)=nan -> 0
    mask = causal[None, None] + keypad                                        # [B,1,L-1,L-1]
    out = model(hin, tin, pos, mask)                                          # [B,L-1,H]
    student = model.lm_head(out)                                             # [B,L-1,V]
    with torch.no_grad():
        teacher = model.lm_head(Hb[:, 1:L])                                  # [B,L-1,V]
    label = ids[:, 2:L]                                                       # [B,L-2] предсказание x_{j+2}
    w = (lm[:, 2:L] > 0) & real[:, 2:L]                                       # валидные и не padding
    n = min(student.shape[1]-1, label.shape[1])
    return student[:, :n], teacher[:, :n], label[:, :n], w[:, :n].float()

@torch.no_grad()
def gate(sample_files, tag):
    model.eval(); corr = tot = 0
    for i in range(0, len(sample_files), BATCH):
        bt = make_batch(sample_files[i:i+BATCH])
        if bt is None: continue
        student, teacher, label, w = run_batch(*bt)
        sp = student.argmax(-1); tp = teacher.argmax(-1)
        corr += ((sp == tp) & (w > 0)).sum().item(); tot += (w > 0).sum().item()
    acc = corr / max(tot, 1)
    log(f"[GATE {tag}] acc_pos0 = {acc:.4f} (tot={tot}) [untrained ждём ~0.66-0.79]")
    return acc

base_acc = gate(held, "untrained")
if base_acc < 0.5:
    log(f"!!! ГЕЙТ ПРОВАЛЕН ({base_acc:.3f}) — конвенция/веса неверны. СТОП."); raise SystemExit(1)
log(f"ГЕЙТ ОК ({base_acc:.3f}).")
if MODE == "gate": raise SystemExit(0)

def kd_loss(student, teacher, label, w):
    # memory-light: top-k KL считаем только по top-k (float лишь [.,K]); hard-CE в bf16 (без full-vocab float)
    K = TOPK_KD if TOPK_KD > 0 else 64
    with torch.no_grad():
        tv, ti = teacher.topk(K, dim=-1)                      # bf16 [.,K]
        tl = F.log_softmax(tv.float() / KD_T, -1); tp = tl.exp()
    sv = student.gather(-1, ti).float()                       # [.,K] float (мелко)
    sl = F.log_softmax(sv / KD_T, -1)
    kl = (tp * (tl - sl)).sum(-1) * (KD_T * KD_T)
    hard = F.cross_entropy(student.reshape(-1, V), label.reshape(-1), reduction="none").view_as(kl)  # bf16 ok
    per = torch.nan_to_num(kl + HARD_W * hard, nan=0.0, posinf=0.0, neginf=0.0)
    return (per * w).sum() / w.sum().clamp_min(1)

train_params = [p for p in model.parameters() if p.requires_grad]
log(f"обучаемых {sum(p.numel() for p in train_params)/1e9:.2f}B")
opt = torch.optim.AdamW(train_params, lr=LR, weight_decay=0.0, betas=(0.9, 0.95))
nbatch = (len(train_files) + BATCH - 1) // BATCH
steps_total = (nbatch * EPOCHS) // ACCUM
warmup = max(1, int(0.05 * steps_total))
def lr_at(s):
    if s < warmup: return LR * s / warmup
    p = (s - warmup) / max(1, steps_total - warmup); return 1e-5 + 0.5*(LR-1e-5)*(1+math.cos(math.pi*p))

model.train(); gstep = 0; micro = 0; opt.zero_grad()
for ep in range(EPOCHS):
    random.shuffle(train_files)
    for bi in range(0, len(train_files), BATCH):
        try:
            bt = make_batch(train_files[bi:bi+BATCH])
            if bt is None: continue
            student, teacher, label, w = run_batch(*bt)
            if w.sum() < 1: continue
            loss = kd_loss(student, teacher, label, w)
            (loss / ACCUM).backward(); micro += 1
            if micro % ACCUM == 0:
                for g in opt.param_groups: g["lr"] = lr_at(gstep)
                torch.nn.utils.clip_grad_norm_(train_params, 1.0)
                opt.step(); opt.zero_grad(); gstep += 1
                if gstep % 10 == 0: log(f"ep{ep} step{gstep}/{steps_total} loss={loss.item():.4f} lr={lr_at(gstep):.2e}")
        except Exception as e:
            log(f"skip batch ({type(e).__name__}: {str(e)[:90]})"); opt.zero_grad(); continue
    gate(held, f"epoch{ep}")
log("обучение готово.")

out_sd = {}
inv = {"fc.weight":"mtp.fc.weight","norm.weight":"mtp.norm.weight",
       "pre_fc_norm_embedding.weight":"mtp.pre_fc_norm_embedding.weight",
       "pre_fc_norm_hidden.weight":"mtp.pre_fc_norm_hidden.weight"}
for mn, t in model.state_dict().items():
    if mn in ("embed_tokens.weight","lm_head.weight") or "rotary" in mn or "inv_freq" in mn: continue
    t = t.to(torch.bfloat16).cpu()
    if mn == "layer.mlp.experts.gate_up_proj":
        for e in range(NEXP):
            g,u = t[e].split(MOE_I, 0)
            out_sd[f"mtp.layers.0.mlp.experts.{e}.gate_proj.weight"] = g.contiguous()
            out_sd[f"mtp.layers.0.mlp.experts.{e}.up_proj.weight"]   = u.contiguous()
    elif mn == "layer.mlp.experts.down_proj":
        for e in range(NEXP): out_sd[f"mtp.layers.0.mlp.experts.{e}.down_proj.weight"] = t[e].contiguous()
    elif mn in inv: out_sd[inv[mn]] = t.contiguous()
    elif mn.startswith("layer."): out_sd["mtp.layers.0."+mn[len("layer."):]] = t.contiguous()
os.makedirs(os.path.dirname(OUT_ST), exist_ok=True)
save_file(out_sd, OUT_ST)
log(f"СОХРАНЕНО {len(out_sd)} mtp.* -> {OUT_ST}")
