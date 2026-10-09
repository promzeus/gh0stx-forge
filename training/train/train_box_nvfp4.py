#!/usr/bin/env python3
"""LoRA on the NVFP4 gh0stx model, on the GB10 box itself (122GB unified). The 84GB
compressed model fits where the 282GB bf16 base never could. run_compressed=True keeps
weights nvfp4-packed (dequant on the fly) so it stays ~84GB. Fail-fast: one forward+
backward before the full run to prove compressed-tensors LoRA works on this GPU."""
import os, json, re, time
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF","expandable_segments:True")
import torch
from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments)
from transformers.utils.quantization_config import CompressedTensorsConfig
from peft import LoraConfig, get_peft_model

BASE = os.environ.get("BASE_DIR", "/home/gh0stx/nvfp4_v4_releases/dflash_only")
DATA = os.environ.get("DATA", "/home/gh0stx/octest/night/dataset/sft.jsonl")
OUT  = os.environ.get("OUT_DIR", "/home/gh0stx/gh0stx-lora-out")
EPOCHS = float(os.environ.get("EPOCHS", "2"))
LR = float(os.environ.get("LR", "2e-4"))
MAXLEN = int(os.environ.get("MAXLEN", "6144"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "8"))
# attention + hybrid linear attn + shared_expert; routed experts stay frozen (nvfp4)
TARGET_RE = (r"(.*\.(q_proj|k_proj|v_proj|o_proj))"
             r"|(.*linear_attn\.(in_proj_qkv|in_proj_a|in_proj_b|in_proj_z|out_proj))"
             r"|(.*shared_expert\.(gate_proj|up_proj|down_proj))")
t0=time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

IM_END="<|im_end|>"; HDR=re.compile(r"<\|im_start\|>assistant\n")
def normalize(msgs):
    out=[]
    for m in msgs:
        m=dict(m)
        if m.get("role")=="assistant" and m.get("tool_calls"):
            tcs=[]
            for tc in m["tool_calls"]:
                tc=dict(tc); fn=dict(tc.get("function",{})); a=fn.get("arguments")
                if isinstance(a,str):
                    try: fn["arguments"]=json.loads(a)
                    except Exception: fn["arguments"]={}
                tc["function"]=fn; tcs.append(tc)
            m["tool_calls"]=tcs
            if m.get("content") is None: m["content"]=""
        out.append(m)
    return out
def build_example(tok, msgs):
    s=tok.apply_chat_template(normalize(msgs), tools=None, tokenize=False, add_generation_prompt=False)
    enc=tok(s, add_special_tokens=False, return_offsets_mapping=True)
    ids,offs=enc["input_ids"],enc["offset_mapping"]
    spans=[]
    for h in HDR.finditer(s):
        st=h.end(); e=s.find(IM_END,st)
        if e!=-1: spans.append((st,e+len(IM_END)))
    labels=[-100]*len(ids)
    for k,(a,b) in enumerate(offs):
        if a==b: continue
        c=(a+b)//2
        if any(a0<=c<b0 for a0,b0 in spans): labels[k]=ids[k]
    return ids[:MAXLEN], labels[:MAXLEN]

class SFT(torch.utils.data.Dataset):
    def __init__(self, tok, path):
        self.rows=[]; skip=0
        for line in open(path):
            line=line.strip()
            if not line: continue
            ids,labels=build_example(tok, json.loads(line)["messages"])
            if not any(l!=-100 for l in labels): skip+=1; continue
            self.rows.append((ids,labels))
        log(f"dataset: {len(self.rows)} usable, {skip} skipped")
    def __len__(self): return len(self.rows)
    def __getitem__(self,i):
        ids,labels=self.rows[i]
        return {"input_ids":ids,"labels":labels,"attention_mask":[1]*len(ids)}
def collate(pad_id):
    def f(b):
        m=max(len(x["input_ids"]) for x in b)
        pad=lambda x,v:x+[v]*(m-len(x))
        return {"input_ids":torch.tensor([pad(x["input_ids"],pad_id) for x in b]),
                "attention_mask":torch.tensor([pad(x["attention_mask"],0) for x in b]),
                "labels":torch.tensor([pad(x["labels"],-100) for x in b])}
    return f

def main():
    os.makedirs(OUT, exist_ok=True)
    tok=AutoTokenizer.from_pretrained(BASE, trust_remote_code=True)
    if tok.pad_token_id is None: tok.pad_token=tok.eos_token
    log("loading nvfp4 compressed model (run_compressed=True, single GPU) ...")
    # transformers' caching_allocator_warmup pre-reserves memory at the DENSE model size
    # (~240GB) and OOMs before compressed weights load. Skip it; the real load allocates
    # only the compressed (nvfp4) footprint.
    import transformers.modeling_utils as _mu
    if hasattr(_mu, "caching_allocator_warmup"):
        _mu.caching_allocator_warmup = lambda *a, **k: None
    # DIAGNOSTIC: the packed-expert converter leaves some keys "missing" -> transformers
    # materializes them DENSE via torch.empty_like on GPU (garbage values, 2.7GB each) -> OOM.
    # Materialize missing keys on CPU instead so the load completes; log count+size and the
    # true post-load GPU footprint. That number decides if nvfp4-on-box training is viable.
    from transformers.modeling_utils import PreTrainedModel as _PTM
    def _diag_move(self, missing_keys, device_map, device_mesh, hf_quantizer):
        mk=list(missing_keys); tied=set(getattr(self,"all_tied_weights_keys",{}).keys())
        tot=0.0
        for k in mk:
            try:
                p=self.get_parameter_or_buffer(k); tot+=p.numel()*p.element_size()
            except Exception: pass
        log(f"[missing] count={len(mk)} tied={len(tied)} dense_total={tot/1e9:.2f}GB")
        for k in mk[:30]:
            try:
                p=self.get_parameter_or_buffer(k)
                log(f"  MK {k} {tuple(p.shape)} {p.dtype} {(p.numel()*p.element_size())/1e9:.3f}GB")
            except Exception as e: log(f"  MK {k} <err {e}>")
        for k in set(mk)-tied:
            param=self.get_parameter_or_buffer(k)
            _mu._load_parameter_into_model(self, k, torch.empty_like(param, device="cpu"))
        for k,buf in self.named_non_persistent_buffers():
            _mu._load_parameter_into_model(self, k, torch.empty_like(buf, device="cuda:0"))
        log("[missing] materialized (missing->CPU, nonpersist-buffers->cuda:0)")
    _PTM._move_missing_keys_from_meta_to_device=_diag_move
    qc=CompressedTensorsConfig(run_compressed=True)
    model=AutoModelForCausalLM.from_pretrained(
        BASE, quantization_config=qc, device_map={"":0}, dtype=torch.bfloat16,
        trust_remote_code=True, low_cpu_mem_usage=True)
    model.config.use_cache=False
    log(f"post-load GPU mem: {torch.cuda.memory_allocated(0)/1e9:.1f} GB")
    for p in model.parameters(): p.requires_grad_(False)
    if hasattr(model,"gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant":False})
    if hasattr(model,"enable_input_require_grads"): model.enable_input_require_grads()
    lora=LoraConfig(r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
                    task_type="CAUSAL_LM", target_modules=TARGET_RE)
    model=get_peft_model(model, lora)
    n=sum(1 for nm,_ in model.named_modules() if nm.endswith(".lora_A.default"))
    tr=sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f"adapted matrices: {n} | trainable params: {tr/1e6:.1f}M")
    assert n>=100, f"regex matched only {n} matrices"

    ds=SFT(tok, DATA)
    log("fail-fast: one forward+backward ...")
    model.train()
    b=collate(tok.pad_token_id)([ds[0]]); dev=next(model.parameters()).device
    b={k:v.to(dev) for k,v in b.items()}
    out=model(**b); log(f"  loss={out.loss.item():.4f}")
    out.loss.backward()
    g=sum(1 for _,p in model.named_parameters() if p.requires_grad and p.grad is not None)
    log(f"  lora params with grad: {g} — forward+backward OK")
    model.zero_grad(set_to_none=True)

    args=TrainingArguments(output_dir=OUT, num_train_epochs=EPOCHS,
        per_device_train_batch_size=1, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03, weight_decay=0.0,
        max_grad_norm=1.0, bf16=True, logging_steps=1, save_strategy="epoch",
        save_total_limit=1, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant":False}, optim="adamw_torch",
        report_to=[], dataloader_num_workers=2, remove_unused_columns=False)
    trainer=Trainer(model=model, args=args, train_dataset=ds, data_collator=collate(tok.pad_token_id))
    log("training ..."); trainer.train()
    model.save_pretrained(OUT); tok.save_pretrained(OUT)
    open(os.path.join(OUT,"DONE"),"w").write(f"n={len(ds)} epochs={EPOCHS}\n")
    log(f"DONE -> {OUT}")

if __name__=="__main__": main()
