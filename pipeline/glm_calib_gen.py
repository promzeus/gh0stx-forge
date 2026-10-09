#!/usr/bin/env python3
"""Offline reasoning-flavored calibration for GLM-4.6 GPTQ. No HF download (the
box's HF pulls stall on the flaky router; self-distill impossible — 302GB won't
fit for generation). A bundled diverse corpus (math word problems, code, offsec,
reasoning prose) is wrapped in the GLM chat template and tokenized. For GPTQ this
only needs to exercise realistic activation distributions, not be ground truth.

Env: CALIB_MODEL (tokenizer dir), CALIB_OUT, CALIB_N, CALIB_SEQ.
"""
import os, json, sys, random
from transformers import AutoTokenizer

MODEL = os.environ.get("CALIB_MODEL", "/data/pruned")
OUT   = os.environ.get("CALIB_OUT", "/data/calib.jsonl")
N     = int(os.environ.get("CALIB_N", "64"))
SEQ   = int(os.environ.get("CALIB_SEQ", "1024"))

tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)

SEEDS = [
    "A snail climbs a 10 m well, +3 m by day and -2 m by night. Which day does it reach the top? Reason step by step.",
    "What is 47 * 89? Show the multiplication steps.",
    "Explain how a hash table resolves collisions with chaining vs open addressing.",
    "Write a Python function that returns the nth Fibonacci number iteratively, then explain its complexity.",
    "A TCP handshake fails only on port 443 but succeeds on 80. List the ordered hypotheses to check.",
    "Given a SQL injection in a login form, describe the steps to confirm and then parameterize the fix.",
    "Prove that the sum of the first n odd numbers equals n squared.",
    "Explain the difference between a race condition and a deadlock, with one concrete example each.",
    "Reverse-engineer what this does: for i in range(len(a)): a[i] ^= key[i % len(key)]. What is it and how to break it?",
    "A pod is Pending with UnfulfillableCapacity on spot. Walk through the diagnosis and the fixes in order.",
    "Compute the GCD of 1071 and 462 using the Euclidean algorithm, showing each step.",
    "Explain how NVFP4 group quantization stores a weight: group size, scale dtype, and the global scale.",
    "Write a Go token-bucket rate limiter and explain the refill math.",
    "Given an HTTP 301 redirect loop between two hosts, enumerate the causes and how to confirm each.",
    "Derive the closed form for 1 + 2 + ... + n and verify it for n = 10.",
    "What does 'ORDER BY 1--' in a URL parameter suggest, and how would you enumerate columns next?",
]

def build_rows():
    # plain tokenization (GLM apply_chat_template(tokenize=True) returns a
    # BatchEncoding, not an id list). Concatenate a few seeds + short reasoning
    # stubs into one text, tokenize, truncate to SEQ.
    rng = random.Random(42)
    rows = []
    attempts = 0
    while len(rows) < N and attempts < N * 40:
        attempts += 1
        picks = rng.sample(SEEDS, k=rng.randint(3, 6))
        parts = []
        for p in picks:
            parts.append(f"User: {p}\nAssistant: Let me reason step by step.\n{p}\n"
                         f"Step 1: identify what is asked. Step 2: work it out carefully. "
                         f"Step 3: verify the result and state the final answer.\n")
        text = "\n".join(parts)
        ids = tok(text).input_ids
        if len(ids) > SEQ:
            ids = ids[:SEQ]
        if len(ids) >= 64:
            rows.append(ids)
    return rows

def main():
    rows = build_rows()
    with open(OUT, "w") as f:
        for ids in rows:
            f.write(json.dumps({"input_ids": ids}) + "\n")
    print(f"[calib] wrote {len(rows)} offline rows (seq<= {SEQ}) -> {OUT}", flush=True)
    return 0 if rows else 1

if __name__ == "__main__":
    sys.exit(main())
