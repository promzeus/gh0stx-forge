#!/usr/bin/env python3
"""
QLoRA SFT for gh0stx (Qwen3_5MoeForCausalLM, ~141B) on the RFT trajectory dataset.

Loads the bf16 base in 4-bit nf4, adapts attention + linear_attn + shared_expert
(routed experts and the router gate are left frozen -> no unused-expert grad issues),
trains on assistant-only loss over the opencode chat trajectories, saves the adapter.

Env:
  BASE_DIR   local path to bf16 base (default /workspace/run397b_v3/decensored_v4)
  DATA       sft jsonl (default /workspace/gh0stx-rft/sft.jsonl)
  OUT_DIR    adapter output (default /workspace/gh0stx-rft/adapter)
  EPOCHS(3) LR(2e-4) R(16) ALPHA(32) DROPOUT(0.05) MAXLEN(8192)
  GRAD_ACCUM(8) SAVE_MERGED(0)
"""
import os, json, re, time, math
import torch
from transformers import (AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig,
                          Trainer, TrainingArguments)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

BASE_DIR = os.environ.get("BASE_DIR", "/workspace/run397b_v3/decensored_v4")
DATA     = os.environ.get("DATA", "/workspace/gh0stx-rft/sft.jsonl")
OUT_DIR  = os.environ.get("OUT_DIR", "/workspace/gh0stx-rft/adapter")
EPOCHS   = float(os.environ.get("EPOCHS", "3"))
LR       = float(os.environ.get("LR", "2e-4"))
R        = int(os.environ.get("R", "16"))
ALPHA    = int(os.environ.get("ALPHA", "32"))
DROPOUT  = float(os.environ.get("DROPOUT", "0.05"))
MAXLEN   = int(os.environ.get("MAXLEN", "8192"))
GRAD_ACCUM = int(os.environ.get("GRAD_ACCUM", "8"))
SAVE_MERGED = os.environ.get("SAVE_MERGED", "0") == "1"

# Attention (q/k/v/o) + hybrid linear attention (GatedDeltaNet) + the always-on
# shared_expert. shared_expert shares leaf names with routed experts, so match by path.
TARGET_RE = (r"(.*\.(q_proj|k_proj|v_proj|o_proj))"
             r"|(.*linear_attn\.(in_proj_qkv|in_proj_a|in_proj_b|in_proj_z|out_proj))"
             r"|(.*shared_expert\.(gate_proj|up_proj|down_proj))")

t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

# ---------- data ----------
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

def _get_layers(model):
    m = getattr(model, "model", model)
    if hasattr(m, "layers"): return m.layers
    if hasattr(m, "language_model"): return m.language_model.layers
    raise RuntimeError("cannot locate decoder layers")

def _pack_layer_from_ckpt(base_dir, wmap, L, num_experts):
    """Read a layer's per-expert gate/up/down from the checkpoint and pack them into
    the model's fused layout: gate_up_proj[e]=cat([gate,up],0) [2*inter,hidden];
    down_proj[e]=down [hidden,inter]."""
    from safetensors import safe_open
    opened = {}
    def get(key):
        sh = wmap[key]
        if sh not in opened:
            opened[sh] = safe_open(os.path.join(base_dir, sh), framework="pt")
        return opened[sh].get_tensor(key)
    pre = f"model.language_model.layers.{L}.mlp.experts"
    g0 = get(f"{pre}.0.gate_proj.weight")           # [inter, hidden]
    inter, hidden = g0.shape
    gu = torch.empty(num_experts, 2 * inter, hidden, dtype=torch.bfloat16)
    dp = torch.empty(num_experts, hidden, inter, dtype=torch.bfloat16)
    for e in range(num_experts):
        g = get(f"{pre}.{e}.gate_proj.weight").to(torch.bfloat16)
        u = get(f"{pre}.{e}.up_proj.weight").to(torch.bfloat16)
        d = get(f"{pre}.{e}.down_proj.weight").to(torch.bfloat16)
        gu[e] = torch.cat([g, u], dim=0)
        dp[e] = d
    return gu, dp

def install_meta_move_patch(base_dir):
    """Patch transformers' meta->device init so the (unpacked) expert keys the buggy
    converter left missing are materialized here — with correctly packed values from
    the checkpoint — right before dispatch_model, instead of crashing on empty_like."""
    import transformers.modeling_utils as _mu
    import re, json
    wmap = json.load(open(os.path.join(base_dir, "model.safetensors.index.json")))["weight_map"]
    E = {}
    def num_experts(L):
        if L not in E:
            E[L] = 1 + max(int(m.group(1)) for k in wmap
                           for m in [re.search(rf"layers\.{L}\.mlp\.experts\.(\d+)\.gate_proj", k)] if m)
        return E[L]
    _cache = {}
    def packed(L):
        if L not in _cache:
            _cache[L] = _pack_layer_from_ckpt(base_dir, wmap, L, num_experts(L))
        return _cache[L]
    _orig = _mu.PreTrainedModel._move_missing_keys_from_meta_to_device
    def _patched(self, missing_keys, device_map, device_mesh, hf_quantizer):
        fixed = []
        remaining = []
        for key in set(missing_keys) - set(self.all_tied_weights_keys.keys()):
            m = re.search(r"layers\.(\d+)\.mlp\.experts\.(gate_up_proj|down_proj)$", key)
            if m:
                L = int(m.group(1)); which = m.group(2)
                gu, dp = packed(L)
                value = gu if which == "gate_up_proj" else dp
                dev = _mu.get_device(device_map, key, valid_torch_device=True)
                _mu._load_parameter_into_model(self, key, value.to(dev))
                fixed.append(key)
            else:
                remaining.append(key)
        for key in remaining:
            param = self.get_parameter_or_buffer(key)
            dev = _mu.get_device(device_map, key, valid_torch_device=True)
            _mu._load_parameter_into_model(self, key, torch.empty_like(param, device=dev))
        for key, buffer in self.named_non_persistent_buffers():
            dev = _mu.get_device(device_map, key, valid_torch_device=True)
            _mu._load_parameter_into_model(self, key, torch.empty_like(buffer, device=dev))
        log(f"meta->device: hand-materialized {len(fixed)} broken expert params, "
            f"{len(remaining)} other missing")
    _mu.PreTrainedModel._move_missing_keys_from_meta_to_device = _patched
    return _mu, _orig

def main_zero3():
    """Fast multi-GPU path: DeepSpeed ZeRO-3 shards the ~270GB bf16 base across all GPUs
    and runs data-parallel (every GPU trains a data shard) — no pipelined device_map, no
    4-bit. Requires the FUSED base (repack_experts.py) so from_pretrained loads under
    zero.Init with no hand-packing patch."""
    os.makedirs(OUT_DIR, exist_ok=True)
    LOCAL_RANK = int(os.environ.get("LOCAL_RANK", "0"))
    log(f"ZERO3 rank={LOCAL_RANK} world={os.environ.get('WORLD_SIZE')} base={BASE_DIR}")
    tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token
    ds = SFT(tok, DATA)

    ds_cfg = os.environ.get("DS_CONFIG", "/code/ds_zero3.json")
    args = TrainingArguments(
        output_dir=OUT_DIR, num_train_epochs=EPOCHS,
        per_device_train_batch_size=int(os.environ.get("PER_DEVICE_BSZ", "1")),
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03,
        weight_decay=0.0, max_grad_norm=1.0, bf16=True,
        logging_steps=1, save_strategy="steps",
        save_steps=int(os.environ.get("SAVE_STEPS", "30")), save_total_limit=1,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[], dataloader_num_workers=2, remove_unused_columns=False,
        deepspeed=ds_cfg,
    )
    # args carries the deepspeed config, so zero3 is active now and from_pretrained shards
    # the base under zero.Init instead of materializing 270GB on one rank.
    log("loading fused base bf16 under zero.Init ...")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_DIR, dtype=torch.bfloat16, trust_remote_code=True)
    model.config.use_cache = False
    model.enable_input_require_grads()

    lora = LoraConfig(r=R, lora_alpha=ALPHA, lora_dropout=DROPOUT, bias="none",
                      task_type="CAUSAL_LM", target_modules=TARGET_RE)
    model = get_peft_model(model, lora)
    n_adapt = sum(1 for n, _ in model.named_modules() if n.endswith(".lora_A.default"))
    log(f"adapted matrices: {n_adapt}")
    assert 200 <= n_adapt <= 900, f"target regex matched {n_adapt} matrices — sanity check failed"

    trainer = Trainer(model=model, args=args, train_dataset=ds,
                      data_collator=collate(tok.pad_token_id))
    log("training (zero3) ...")
    trainer.train()
    log("saving adapter (deepspeed gather) ...")
    trainer.save_model(OUT_DIR)
    if LOCAL_RANK == 0:
        tok.save_pretrained(OUT_DIR)
        with open(os.path.join(OUT_DIR, "DONE"), "w") as f:
            f.write(f"zero3 epochs={EPOCHS} lr={LR} r={R} alpha={ALPHA} n={len(ds)}\n")
        log(f"DONE -> {OUT_DIR}")

def main_orpo():
    """Commit-fix preference round via DPO (TRL 1.x dropped ORPOTrainer; ORPO is gone as a
    loss_type too). DPOTrainer with peft_config and ref_model=None uses the base with the
    adapter disabled as the reference -> no second full model, fits ZeRO-3 like SFT. Pairs:
    chosen = terse committed answer, rejected = the model's verbose/looping non-commit.
    Trains on v5-merged so SFT knowledge stays; only delivery behavior shifts."""
    import datasets as _ds
    from datasets import Dataset
    from trl import DPOTrainer, DPOConfig
    # precompute_ref_log_probs runs datasets.map to cache reference logps; the 8 torchrun
    # ranks share one container /tmp and race on the arrow cache file (FileNotFoundError on
    # a renamed temp). Keep everything in memory so no arrow cache is written.
    _ds.disable_caching()
    os.makedirs(OUT_DIR, exist_ok=True)
    LOCAL_RANK = int(os.environ.get("LOCAL_RANK", "0"))
    log(f"DPO(commit) rank={LOCAL_RANK} world={os.environ.get('WORLD_SIZE')} base={BASE_DIR} data={DATA}")
    tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token

    rows = [json.loads(l) for l in open(DATA) if l.strip()]
    def render(r):
        prompt = tok.apply_chat_template(
            [{"role": "user", "content": r["prompt"]}],
            tools=None, tokenize=False, add_generation_prompt=True)
        return {"prompt": prompt, "chosen": r["chosen"], "rejected": r["rejected"]}
    ds = Dataset.from_list([render(r) for r in rows])
    log(f"preference pairs: {len(ds)}")

    ds_cfg = os.environ.get("DS_CONFIG", "/code/ds_zero3.json")
    args = DPOConfig(
        output_dir=OUT_DIR, num_train_epochs=EPOCHS,
        per_device_train_batch_size=int(os.environ.get("PER_DEVICE_BSZ", "1")),
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03,
        weight_decay=0.0, max_grad_norm=1.0, bf16=True,
        beta=float(os.environ.get("DPO_BETA", os.environ.get("ORPO_BETA", "0.1"))),
        loss_type=[os.environ.get("DPO_LOSS", "sigmoid")],
        max_length=MAXLEN,
        # DPO doubles the forward (chosen+rejected) and adds a reference pass -> ~3-4x SFT
        # activation memory. precompute_ref_log_probs runs the reference once up front so
        # training is single-forward like SFT; activation_offloading spills activations to
        # CPU. Both keep the concat forward under the H200 ceiling.
        precompute_ref_log_probs=os.environ.get("PRECOMPUTE_REF", "1") == "1",
        activation_offloading=os.environ.get("ACT_OFFLOAD", "1") == "1",
        logging_steps=1, save_strategy="steps",
        save_steps=int(os.environ.get("SAVE_STEPS", "50")), save_total_limit=1,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[], dataloader_num_workers=2, remove_unused_columns=False,
        deepspeed=ds_cfg,
    )
    # DPOConfig carries the deepspeed config -> zero.Init shards the base at from_pretrained.
    log("loading v5-merged base bf16 under zero.Init ...")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_DIR, dtype=torch.bfloat16, trust_remote_code=True)
    model.config.use_cache = False
    model.enable_input_require_grads()

    lora = LoraConfig(r=R, lora_alpha=ALPHA, lora_dropout=DROPOUT, bias="none",
                      task_type="CAUSAL_LM", target_modules=TARGET_RE)
    # ref_model=None + peft_config -> reference = base with adapter disabled (no 2nd model).
    trainer = DPOTrainer(model=model, ref_model=None, args=args, train_dataset=ds,
                         processing_class=tok, peft_config=lora)
    log("training (dpo commit) ...")
    trainer.train()
    log("saving adapter (deepspeed gather) ...")
    trainer.save_model(OUT_DIR)
    if LOCAL_RANK == 0:
        tok.save_pretrained(OUT_DIR)
        with open(os.path.join(OUT_DIR, "DONE"), "w") as f:
            f.write(f"dpo-commit epochs={EPOCHS} lr={LR} beta={args.beta} r={R} n={len(ds)}\n")
        log(f"DONE -> {OUT_DIR}")

def main():
    if os.environ.get("ORPO", "0") == "1":
        return main_orpo()
    if os.environ.get("ZERO3", "0") == "1":
        return main_zero3()
    os.makedirs(OUT_DIR, exist_ok=True)
    log(f"base={BASE_DIR}  data={DATA}  out={OUT_DIR}")
    tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)
    if tok.pad_token_id is None: tok.pad_token = tok.eos_token

    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                             bnb_4bit_use_double_quant=True,
                             bnb_4bit_compute_dtype=torch.bfloat16)
    # config.json has torch_dtype=null, so the auto device_map planner sizes the model
    # at fp32 (~564GB) and spills MoE layers to CPU, which bnb 4-bit rejects. Build the
    # map ourselves on a meta model with an explicit bf16 estimate -> GPU-only placement.
    LOCAL_RANK = int(os.environ.get("LOCAL_RANK", -1))
    FAST_DDP = os.environ.get("FAST_DDP", "0") == "1" and LOCAL_RANK >= 0
    if FAST_DDP:
        # Data-parallel fast path: every process holds the FULL 4-bit model (~80GB) on its
        # own GPU and trains a data shard; Trainer wraps DDP so only the small LoRA grads
        # all-reduce. ~8x the single-replica pipelined device_map, and no base repack — the
        # meta->device packing patch works per-process against a single-device map.
        torch.cuda.set_device(LOCAL_RANK)
        dmap = {"": LOCAL_RANK}
        log(f"FAST_DDP rank={LOCAL_RANK} world={os.environ.get('WORLD_SIZE')}: "
            f"full 4-bit model on cuda:{LOCAL_RANK}")
    else:
        from accelerate import init_empty_weights, infer_auto_device_map
        from transformers import AutoConfig
        ngpu = torch.cuda.device_count()
        per_gpu = os.environ.get("PER_GPU_MEM", "38GiB")
        cfg = AutoConfig.from_pretrained(BASE_DIR, trust_remote_code=True)
        with init_empty_weights():
            meta = AutoModelForCausalLM.from_config(cfg, trust_remote_code=True)
        meta.tie_weights()
        no_split = getattr(meta, "_no_split_modules", None)
        if not no_split:
            dec = getattr(getattr(meta, "model", meta), "layers", None)
            if dec is None:
                dec = meta.model.language_model.layers
            no_split = [dec[0].__class__.__name__]
        dmap = infer_auto_device_map(meta, max_memory={i: per_gpu for i in range(ngpu)},
                                     dtype=torch.bfloat16, no_split_module_classes=no_split)
        del meta
        on_cpu = [k for k, v in dmap.items() if v in ("cpu", "disk")]
        log(f"device_map over {ngpu} GPUs @ {per_gpu} | no_split={no_split} | on_cpu={on_cpu}")
        assert not on_cpu, f"planner still placed modules on CPU: {on_cpu}"
    # transformers' packed-expert converter deterministically fails to concat gate_up_proj
    # for a handful of layers in this checkpoint (raises during finalize). Suppress the
    # fatal report, capture which target keys failed, then repack those experts by hand.
    import transformers.modeling_utils as _mu
    _orig_report = _mu.log_state_dict_report
    def _patched_report(*a, **k):
        try:
            return _orig_report(*a, **k)
        except RuntimeError as e:
            log(f"suppressed conversion report ({str(e)[:80]}...) — experts hand-packed")
    _mu.log_state_dict_report = _patched_report
    _mm, _orig_move = install_meta_move_patch(BASE_DIR)
    log("loading base in 4-bit nf4 (manual bf16-sized device_map) ...")
    model = AutoModelForCausalLM.from_pretrained(
        BASE_DIR, quantization_config=bnb, device_map=dmap,
        dtype=torch.bfloat16, trust_remote_code=True, low_cpu_mem_usage=True)
    _mu.log_state_dict_report = _orig_report
    _mm.PreTrainedModel._move_missing_keys_from_meta_to_device = _orig_move
    model.config.use_cache = False
    mem = [round(torch.cuda.memory_allocated(i)/1e9, 1) for i in range(torch.cuda.device_count())]
    log(f"post-load GPU mem (GB): {mem}  (4-bit base should be ~10/GPU, leaving room to train)")
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    lora = LoraConfig(r=R, lora_alpha=ALPHA, lora_dropout=DROPOUT, bias="none",
                      task_type="CAUSAL_LM", target_modules=TARGET_RE)
    model = get_peft_model(model, lora)
    n_adapt = sum(1 for n,_ in model.named_modules() if n.endswith(".lora_A.default"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f"adapted matrices: {n_adapt} | trainable params: {trainable/1e6:.1f}M")
    assert 200 <= n_adapt <= 900, f"target regex matched {n_adapt} matrices — sanity check failed"
    model.enable_input_require_grads()
    model.config.use_cache = False

    ds = SFT(tok, DATA)
    args = TrainingArguments(
        output_dir=OUT_DIR, num_train_epochs=EPOCHS,
        per_device_train_batch_size=1, gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR, lr_scheduler_type="cosine", warmup_ratio=0.03,
        weight_decay=0.0, max_grad_norm=1.0, bf16=True,
        logging_steps=1, save_strategy="steps",
        save_steps=int(os.environ.get("SAVE_STEPS","25")), save_total_limit=1,
        gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit", report_to=[], dataloader_num_workers=2,
        remove_unused_columns=False,
        # DDP: LoRA params are all used every forward, so disable the unused-param scan
        # (which also conflicts with non-reentrant gradient checkpointing).
        ddp_find_unused_parameters=False,
    )
    trainer = Trainer(model=model, args=args, train_dataset=ds,
                      data_collator=collate(tok.pad_token_id))
    log("training ...")
    trainer.train()
    # Under DDP every rank reaches here; only rank 0 writes the adapter and merges.
    is_main = LOCAL_RANK <= 0
    if not is_main:
        log(f"rank {LOCAL_RANK}: training done, skipping save/merge (rank 0 owns it)")
        return
    log("saving adapter ...")
    model.save_pretrained(OUT_DIR)
    tok.save_pretrained(OUT_DIR)
    with open(os.path.join(OUT_DIR, "DONE"), "w") as f:
        f.write(f"epochs={EPOCHS} lr={LR} r={R} alpha={ALPHA} n={len(ds)}\n")
    log(f"DONE -> {OUT_DIR}")

    if SAVE_MERGED:
        log("merging adapter into bf16 (this needs lots of RAM) ...")
        from peft import PeftModel
        del model; torch.cuda.empty_cache()
        base = AutoModelForCausalLM.from_pretrained(BASE_DIR, dtype=torch.bfloat16,
                    device_map="cpu", trust_remote_code=True, low_cpu_mem_usage=True)
        merged = PeftModel.from_pretrained(base, OUT_DIR).merge_and_unload()
        mout = os.environ.get("MERGED_DIR", "/workspace/gh0stx-rft/merged")
        merged.save_pretrained(mout, safe_serialization=True, max_shard_size="10GB")
        tok.save_pretrained(mout)
        log(f"merged -> {mout}")

if __name__ == "__main__":
    main()
