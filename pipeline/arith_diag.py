#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arith_diag.py — изолировать этап деградации арифметики.
Грузит BF16-чекпоинт (MODEL_DIR) через transformers device_map=auto (offload на CPU-RAM если не влезает),
гоняет те же примеры, что провалила nvfp4_v3c (256-89, 512-137) + контроли, thinking-off И thinking-on.
Печатает HIT/MISS + фактический (неверный) ответ — арифметика бенайн, вывод безопасен.
"""
import os, re, torch
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_DIR = os.environ["MODEL_DIR"]
TAG = os.environ.get("TAG", MODEL_DIR)
print(f"[diag] loading {TAG} from {MODEL_DIR} ...", flush=True)
tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
# device_map="auto" без лимитов перегружает крайний GPU → CUDA OOM при загрузке (QUANT-DEPLOY урок).
# Даём headroom: кап 36GiB на GPU (из ~44) + остаток на CPU-offload. 262G BF16 → ~144G GPU + ~118G CPU.
n_gpu = torch.cuda.device_count()
max_mem = {i: "36GiB" for i in range(n_gpu)}
max_mem["cpu"] = "600GiB"
print(f"[diag] n_gpu={n_gpu} max_memory={max_mem}", flush=True)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_DIR, torch_dtype=torch.bfloat16, device_map="auto", max_memory=max_mem,
    low_cpu_mem_usage=True, trust_remote_code=True,
)
model.eval()
print(f"[diag] loaded. hf_device_map buckets: {len(set(str(v) for v in getattr(model,'hf_device_map',{}).values()))}", flush=True)

PROBES = [
    ("256 минус 89", "167"),
    ("512 минус 137", "375"),
    ("143 плюс 78", "221"),
    ("84 умножить на 12", "1008"),
    ("1000 минус 333", "667"),
    ("47 плюс 68", "115"),
]

def run(prompt, think, max_new):
    msgs = [{"role": "user", "content": prompt + " Ответь одним числом."}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True,
                                   enable_thinking=think)
    inp = tok(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(**inp, max_new_tokens=max_new, do_sample=False, temperature=None, top_p=None)
    gen = tok.decode(out[0][inp["input_ids"].shape[1]:], skip_special_tokens=True)
    tail = gen.split("</think>")[-1].strip()
    return gen, tail

for think, max_new, label in [(False, 64, "thinking-OFF"), (True, 1200, "thinking-ON")]:
    print(f"\n===== {TAG} :: {label} =====", flush=True)
    hit = 0
    for p, exp in PROBES:
        try:
            gen, tail = run(p, think, max_new)
        except Exception as e:
            print(f"  {p} = {exp}: ERROR {e}", flush=True); continue
        ok = exp in tail if tail else exp in gen
        hit += ok
        show = (tail or gen)[:50].replace("\n", " ")
        print(f"  {p} = {exp}: {'HIT' if ok else 'MISS'}  ans={show!r}", flush=True)
    print(f"  --> {TAG} {label}: {hit}/{len(PROBES)}", flush=True)
print(f"\n[diag] DONE {TAG}", flush=True)
