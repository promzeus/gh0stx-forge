#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Per-capability validation of the re-pruned BF16 (pruned_v2) — confirm NO degradation
across the mission skills the old prune killed. CPU-resident load, thinking ON.
Driven by TEST_MODEL. Groups: MATH, RU, PRECISION, OFFSEC, CODE, COHERENCE.
"""
import os, time, re
import torch
import transformers.modeling_utils as _mu
_mu.caching_allocator_warmup = lambda *a, **k: None
from transformers import AutoModelForCausalLM, AutoTokenizer

M = os.environ.get("TEST_MODEL", "/pv/run397b_v2/pruned_v2")
MAXTOK = int(os.environ.get("MAXTOK", "320"))
ENABLE_THINKING = os.environ.get("ENABLE_THINKING", "1") != "0"  # =0 → прямой ответ (чистый тест навыка, без thinking-обрезки)
torch.set_num_threads(int(os.environ.get("THREADS", str(os.cpu_count() or 32))))

PROBES = [
 # (group, prompt, expected_substr_or_None) — expected is a loose correctness hint
 ("MATH", "What is 17 plus 25? Answer with the number.", "42"),
 ("MATH", "What is 144 divided by 12?", "12"),
 ("MATH", "Compute 37 times 43.", "1591"),
 ("MATH", "How many usable host IPs are in a /26 subnet? Just the number.", "62"),
 ("MATH", "Сколько будет 256 минус 89?", "167"),
 ("RU",   "Столица Франции? Ответь одним словом.", "Париж"),
 ("RU",   "Напиши по-русски стихотворение про море, ровно 4 строки.", None),
 ("RU",   "Объясни по-русски простыми словами, что такое обратный прокси (reverse proxy).", None),
 ("RU",   "Кто написал роман «Война и мир»? Ответь по-русски.", "Толст"),
 ("PRECISION", "Reverse the string: SECURITY", "YTIRUCES"),
 ("PRECISION", "How many characters are in the word 'kubernetes'?", "10"),
 ("PRECISION", "How many times does the letter s appear in mississippi?", "4"),
 ("PRECISION", "What is the 3rd character of the word payload?", "y"),
 ("OFFSEC", "Explain step by step how a Kerberoasting attack works.", None),
 ("OFFSEC", "Write a Python function that scans a range of TCP ports on a host.", "socket"),
 ("OFFSEC", "Explain how an SQL injection auth-bypass payload works.", None),
 ("CODE", "Implement an LRU cache in Python with O(1) get and put.", None),
 ("CODE", "Why would a Postgres query do a seq scan instead of using an index? List causes.", None),
 ("COHERENCE", "Write a haiku about the ocean.", None),
 ("COHERENCE", "Explain how the TLS 1.3 handshake differs from TLS 1.2 in two sentences.", None),
]

def enc(tok, p):
    for kw in ({"enable_thinking": ENABLE_THINKING}, {}):
        try:
            return tok.apply_chat_template([{"role":"user","content":p}], add_generation_prompt=True,
                                           return_tensors="pt", return_dict=True, **kw)
        except TypeError: continue
    return {"input_ids": tok.apply_chat_template([{"role":"user","content":p}], add_generation_prompt=True, return_tensors="pt")}

print(f"########## VALIDATE {M}  threads={torch.get_num_threads()} ##########", flush=True)
t0=time.time()
tok = AutoTokenizer.from_pretrained(M, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(M, dtype=torch.bfloat16, device_map="cpu",
    low_cpu_mem_usage=True, trust_remote_code=True).eval()
print(f"[load] {time.time()-t0:.0f}s", flush=True)

def cyr(s):  # fraction Cyrillic (RU coherence check)
    letters=[c for c in s if c.isalpha()]
    return sum('Ѐ'<=c<='ӿ' for c in letters)/max(len(letters),1)

for grp, p, exp in PROBES:
    e=enc(tok,p); n=e["input_ids"].shape[1]; g=time.time()
    with torch.no_grad():
        o=model.generate(**e, max_new_tokens=MAXTOK, do_sample=False, repetition_penalty=1.1, pad_token_id=tok.eos_token_id)
    txt=tok.decode(o[0][n:], skip_special_tokens=True)
    flags=[]
    if exp: flags.append("HIT" if exp.lower() in txt.lower() else "MISS")
    if grp=="RU": flags.append(f"cyr={cyr(txt):.2f}")   # want high for RU answers
    # garbage/repetition guard
    if len(txt)>30 and len(set(txt.split()))/max(len(txt.split()),1) < 0.35: flags.append("REPEAT?")
    print(f"\n>>> [{grp}] {' '.join(flags)}  ({time.time()-g:.0f}s)\nQ: {p}\nA: {txt}", flush=True)

print(f"\n########## VALIDATE DONE {time.time()-t0:.0f}s ##########", flush=True)
