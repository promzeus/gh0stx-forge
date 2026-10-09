#!/usr/bin/env python3
"""Merge the trained LoRA adapter into the fused bf16 base on CPU (rank 0, no deepspeed).
Output feeds the NVFP4 requant + gx10 deploy. LoRA targets attention/linear_attn/
shared_expert only, whose module names are unchanged by the expert fusion, so the fused
base merges cleanly."""
import os, torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

BASE_DIR = os.environ["BASE_DIR"]
OUT_DIR  = os.environ["OUT_DIR"]
MERGED   = os.environ.get("MERGED_DIR", "/data/merged")

print(f"merge: base={BASE_DIR} adapter={OUT_DIR} -> {MERGED}", flush=True)
tok = AutoTokenizer.from_pretrained(BASE_DIR, trust_remote_code=True)
base = AutoModelForCausalLM.from_pretrained(
    BASE_DIR, dtype=torch.bfloat16, device_map="cpu",
    trust_remote_code=True, low_cpu_mem_usage=True)
merged = PeftModel.from_pretrained(base, OUT_DIR).merge_and_unload()
merged.save_pretrained(MERGED, safe_serialization=True, max_shard_size="10GB")
tok.save_pretrained(MERGED)
print(f"merged -> {MERGED}", flush=True)
