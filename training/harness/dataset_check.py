#!/usr/bin/env python3
"""Dataset check (spec section 33 'dataset check'). Render each SFT row with the same
chat template the trainer uses and confirm assistant segments get labeled. A row with
zero labeled tokens is skipped by train_qlora_nvfp4.py, so it is reported UNUSABLE here.
Mirrors build_example() from train_qlora_nvfp4.py without importing peft/torch.
usage: BASE_DIR=<nvfp4 base> dataset_check.py <sft.jsonl>
"""
import os, re, json, sys
from transformers import AutoTokenizer

MAXLEN = int(os.environ.get("MAXLEN", "8192"))
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


def labeled(tok, msgs):
    s = tok.apply_chat_template(normalize(msgs), tools=None, tokenize=False,
                                add_generation_prompt=False)
    enc = tok(s, add_special_tokens=False, return_offsets_mapping=True)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    spans = []
    for h in HDR.finditer(s):
        st = h.end(); e = s.find(IM_END, st)
        if e != -1: spans.append((st, e + len(IM_END)))
    n = 0
    for (a, b) in offs:
        if a == b: continue
        c = (a + b) // 2
        if any(a0 <= c < b0 for a0, b0 in spans): n += 1
    return min(len(ids), MAXLEN), n


def main():
    base = os.environ.get("BASE_DIR")
    if not base:
        print("BASE_DIR not set"); sys.exit(2)
    tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    usable = bad = 0
    for i, line in enumerate(open(sys.argv[1]), 1):
        line = line.strip()
        if not line: continue
        r = json.loads(line)
        total, lab = labeled(tok, r["messages"])
        if lab > 0: usable += 1
        else: bad += 1
        print(f"row {i} {r.get('task_id','?')}: tokens={total} labeled={lab} "
              f"[{'usable' if lab > 0 else 'UNUSABLE'}]")
    print(f"summary: {usable} usable, {bad} unusable")
    sys.exit(0 if bad == 0 and usable > 0 else 1)


if __name__ == "__main__":
    main()
