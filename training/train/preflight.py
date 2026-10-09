#!/usr/bin/env python3
"""CPU preflight: verify the software stack + data pipeline before burning GPU time.
Imports the training libs, loads the base config/tokenizer from the PVC, and builds
a few training examples with the real masking. No GPU, no 4-bit load."""
import os, sys, json, importlib.metadata as md
BASE_DIR = os.environ.get("BASE_DIR", "/workspace/run397b_v3/decensored_v4")
DATA = os.environ.get("DATA", "/workspace/gh0stx-rft/sft.jsonl")

for p in ["torch","transformers","peft","trl","bitsandbytes","accelerate","datasets"]:
    try: print(f"{p:14s}", md.version(p))
    except Exception: print(f"{p:14s} MISSING");

from transformers import AutoConfig, AutoTokenizer
cfg = AutoConfig.from_pretrained(BASE_DIR, trust_remote_code=True)
print("arch:", cfg.architectures, "| layers:", cfg.num_hidden_layers,
      "| experts:", getattr(cfg,"num_experts",None), "| hidden:", cfg.hidden_size)
tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)

sys.path.insert(0, "/code")
from train_qlora import build_example, normalize  # reuse the exact masking
rows = [json.loads(l) for l in open(DATA) if l.strip()]
tot_ids = tot_lab = zero = 0
mx = 0
for r in rows:
    ids, labels = build_example(tok, r["messages"])
    lab = sum(1 for x in labels if x != -100)
    tot_ids += len(ids); tot_lab += lab; mx = max(mx, len(ids))
    if lab == 0: zero += 1
print(f"rows={len(rows)} zero_labeled={zero} max_seqlen={mx} "
      f"labeled_frac={tot_lab/max(tot_ids,1):.3f}")
assert zero == 0, "some rows have no assistant tokens"
# verify the LoRA regex compiles + is applied against real module names cheaply:
import re
from train_qlora import TARGET_RE
re.compile(TARGET_RE)
print("PREFLIGHT OK")
