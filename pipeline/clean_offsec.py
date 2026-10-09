#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_offsec.py — чистка corpus_offsec.json перед калибровкой прунинга.
- разэкранирует ТОЛЬКО литеральные \\n и \\t → настоящие переносы (75% записей).
  \\x и \\u НЕ трогаем — это может быть настоящий шеллкод (\\x90...) в offsec-контенте.
- выкидывает рефузалы/дисклеймеры (не место в uncensored offsec), near-дубли, слишком короткие.
- обрезки (~6000) и markdown ОСТАВЛЯЕМ (начало валидное / структурные writeup'ы).
Вход: CORPUS_DIR/corpus_offsec.json  Выход: CORPUS_DIR/corpus_offsec_clean.json
"""
import json, re, hashlib, collections, os

CORPUS = os.environ.get("CORPUS_DIR", "/Users/flash/gb10/pipeline/corpus")
SRC = os.path.join(CORPUS, "corpus_offsec.json")
OUT = os.path.join(CORPUS, "corpus_offsec_clean.json")

REFUSALS = ["i cannot", "i can't", "i'm sorry", "i am sorry", "as an ai", "i'm unable",
    "i am unable", "cannot assist", "can't help", "cannot provide", "not able to",
    "i must decline", "against my", "i do not condone", "educational purposes only",
    "i won't provide", "i will not provide", "for ethical", "disclaimer:",
    "please note that i", "it is illegal", "i'm not able"]

def norm(s): return re.sub(r"\s+", " ", s.lower()).strip()

d = json.load(open(SRC))
n0 = len(d)
out, seen = [], set()
drop = collections.Counter()
for x in d:
    y = x.replace("\\n", "\n").replace("\\t", "\t")   # только форматные escape
    if len(y) < 300:
        drop["short"] += 1; continue
    yl = y.lower()
    if any(r in yl for r in REFUSALS):
        drop["refusal"] += 1; continue
    k = hashlib.md5(norm(y)[:400].encode()).hexdigest()
    if k in seen:
        drop["neardup"] += 1; continue
    seen.add(k); out.append(y)

json.dump(out, open(OUT, "w"), ensure_ascii=False)
print(f"вход:  {n0}")
print(f"выкинуто: refusal={drop['refusal']} neardup={drop['neardup']} short={drop['short']}")
print(f"выход: {len(out)} ({100*len(out)//n0}%) → {OUT}")
# контроль: остались ли литеральные \n / рефузалы
lit = sum(1 for x in out if "\\n" in x)
ref = sum(1 for x in out if any(r in x.lower() for r in REFUSALS))
print(f"контроль: с литеральным '\\\\n' осталось={lit}, с рефузалами={ref}")
