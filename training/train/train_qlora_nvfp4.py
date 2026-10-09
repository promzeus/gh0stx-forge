#!/usr/bin/env python3
"""QLoRA on the NVFP4 compressed-tensors base (gh0stx). Fits available p4d (8xA100-40):
the compressed model is ~84GB, so no bnb, no bf16-expert memory wall. LoRA trains on top
of the frozen compressed base. Fail-fast: one forward+backward before the full run to
confirm NVFP4 dequant works on Ampere."""
import os, json, re, time
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, Trainer, TrainingArguments
from peft import LoraConfig, get_peft_model

BASE_DIR = os.environ.get("BASE_DIR", "/base")
DATA     = os.environ.get("DATA", "/data/sft.jsonl")
OUT_DIR  = os.environ.get("OUT_DIR", "/data/adapter")
EPOCHS   = float(os.environ.get("EPOCHS", "3"))
LR       = float(os.environ.get("LR", "2e-4"))
R        = int(os.environ.get("R", "16"))
ALPHA    = int(os.environ.get("ALPHA", "32"))
DROPOUT  = float(os.environ.get("DROPOUT", "0.05"))
MAXLEN   = int(os.environ.get("MAXLEN", "8192"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "8"))
TARGET_RE = (r"(.*\.(q_proj|k_proj|v_proj|o_proj))"
             r"|(.*linear_attn\.(in_proj_qkv|in_proj_a|in_proj_b|in_proj_z|out_proj))"
             r"|(.*shared_expert\.(gate_proj|up_proj|down_proj))")
t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

IM_END = "<|im_end|>"
HDR = re.compile(r"<\|im_start\|>assistant\n")

def normalize(msgs):
    out = []
    for m in msgs:
        m = dict(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = []
            for tc in m["tool_calls"]:
                tc = dict(tc); fn = dict(tc.get("function", {}))
                a = fn.get("arguments")
                if isinstance(a, str):
                    try: fn["arguments"] = json.loads(a)
                    except Exception: fn["arguments"] = {}
                tc["function"] = fn; tcs.append(tc)
            m["tool_calls"] = tcs
            if m.get("content") is None: m["content"] = ""
        out.append(m)
    return out

def build_example(tok, msgs):
    """Render with the chat template; label only assistant sections (content +
    tool_call + the closing <|im_end|>). Everything else is masked to -100."""
    s = tok.apply_chat_template(normalize(msgs), tools=None, tokenize=False,
                                add_generation_prompt=False)
    enc = tok(s, add_special_tokens=False, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    spans = []
    for h in HDR.finditer(s):
        start = h.end(); e = s.find(IM_END, start)
        if e != -1: spans.append((start, e + len(IM_END)))
    labels = [-100] * len(ids)
    for k, (a, b) in enumerate(offs):
        if a == b: continue
        c = (a + b) // 2
        if any(a0 <= c < b0 for a0, b0 in spans):
            labels[k] = ids[k]
    return ids[:MAXLEN], labels[:MAXLEN]

class SFT(torch.utils.data.Dataset):
    def __init__(self, tok, path):
        self.rows = []
        skipped = 0
        for line in open(path):
            line = line.strip()
            if not line: continue
            r = json.loads(line)
            ids, labels = build_example(tok, r["messages"])
            if not any(l != -100 for l in labels):
                skipped += 1; continue
            self.rows.append((ids, labels))
        log(f"dataset: {len(self.rows)} usable, {skipped} skipped (no assistant tokens)")
    def __len__(self): return len(self.rows)
    def __getitem__(self, i):
        ids, labels = self.rows[i]
        return {"input_ids": ids, "labels": labels, "attention_mask": [1]*len(ids)}

def collate(pad_id):
    def f(batch):
        m = max(len(b["input_ids"]) for b in batch)
        import torch as T
        def pad(x, v): return x + [v]*(m-len(x))
        return {
            "input_ids": T.tensor([pad(b["input_ids"], pad_id) for b in batch]),
            "attention_mask": T.tensor([pad(b["attention_mask"], 0) for b in batch]),
            "labels": T.tensor([pad(b["labels"], -100) for b in batch]),
        }
    return f


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"base={BASE_DIR} (nvfp4 compressed) data={DATA} out={OUT_DIR}")
    tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token
    log("loading nvfp4 compressed base (device_map=auto) ...")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_DIR, device_map="auto", dtype=torch.bfloat16, trust_remote_code=True,
        low_cpu_mem_usage=True)
    model.config.use_cache = False
    mem = [round(torch.cuda.memory_allocated(i)/1e9,1) for i in range(torch.cuda.device_count())]
    log(f"post-load GPU mem (GB): {mem}")
    for p in model.parameters(): p.requires_grad_(False)
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    if hasattr(model, "enable_input_require_grads"): model.enable_input_require_grads()
    lora = LoraConfig(r=R, lora_alpha=ALPHA, lora_dropout=DROPOUT, bias="none",
                      task_type="CAUSAL_LM", target_modules=TARGET_RE)
    model = get_peft_model(model, lora)
    n_adapt = sum(1 for n,_ in model.named_modules() if n.endswith(".lora_A.default"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f"adapted matrices: {n_adapt} | trainable params: {trainable/1e6:.1f}M")
    assert n_adapt >= 200, f"target regex matched only {n_adapt} matrices"

    ds = SFT(tok, DATA)
    # fail-fast: one forward+backward to confirm NVFP4 dequant + autograd on this GPU
    log("fail-fast: single forward+backward ...")
    model.train()
    b = collate(tok.pad_token_id)([ds[0]])
    dev = next(model.parameters()).device
    b = {k: v.to(dev) for k,v in b.items()}
    out = model(**b)
    log(f"  loss={out.loss.item():.4f}")
    out.loss.backward()
    g = sum(1 for _,p in model.named_parameters() if p.requires_grad and p.grad is not None)
    log(f"  lora params with grad: {g} — forward+backward OK")
    model.zero_grad(set_to_none=True)

    args = TrainingArguments(
        output_dir=OUT_DIR, num_train_epochs=EPOCHS,
        per_device_train_batch_size=1, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03,
        weight_decay=0.0, max_grad_norm=1.0, bf16=True, logging_steps=1,
        save_strategy="epoch", save_total_limit=1, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, optim="adamw_torch",
        report_to=[], dataloader_num_workers=2, remove_unused_columns=False)
    trainer = Trainer(model=model, args=args, train_dataset=ds,
                      data_collator=collate(tok.pad_token_id))
    log("training ...")
    trainer.train()
    model.save_pretrained(OUT_DIR); tok.save_pretrained(OUT_DIR)
    open(os.path.join(OUT_DIR,"DONE"),"w").write(f"nvfp4 base, n={len(ds)}\n")
    log(f"DONE -> {OUT_DIR}")

if __name__ == "__main__":
    main()
