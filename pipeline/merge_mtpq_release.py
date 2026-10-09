#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_mtpq_release.py — собрать новый release-каталог из NVFP4-квантованной
MTP-головы (выход nvfp4_quant_mtp.py) + основного ствола предыдущего релиза.

Вход:
  BASE_RELEASE   -- существующий рабочий релиз (calibrated_v1): ствол NVFP4 +
                    grafted BF16 mtp overlay + config.json/index.json
  MTPQ_SHARD     -- model-mtp-quantized.safetensors (полный mtp.* неймспейс,
                    эксперты/shared_expert/self_attn -> NVFP4 weight_packed,
                    fc/нормы/gate -> BF16)
  OUT_RELEASE    -- новый каталог релиза (calibrated_mtpq_v1)

Что делает:
  1. hardlink ствольных шардов (model-00001/00002) + tokenizer/generation/
     preprocessor/processor/chat_template (без копирования гигабайт).
  2. copy MTPQ_SHARD.
  3. index.json: убрать ВСЕ старые mtp.* ключи, добавить ВСЕ ключи из MTPQ_SHARD
     (указывают на новый шард), пересчитать total_size из хедеров (дёшево).
  4. config.json: в quantization_config.ignore заменить широкий 're:.*mtp.*'
     на узкий 're:.*mtp\\.fc' — тогда квантованные mtp-эксперты/self_attn/
     shared_expert перестают игнорироваться (грузятся как NVFP4, как у ствола),
     а нормы/роутер/shared_expert_gate остаются BF16 через УЖЕ существующие
     generic-регэкспы ствола (re:.*norm.*, re:.*mlp\\.gate$, re:.*shared_expert_gate$),
     mtp.fc остаётся BF16 через новый узкий паттерн.
"""
import json
import os
import shutil
import sys

from safetensors import safe_open

BASE_RELEASE = os.environ["BASE_RELEASE"]
MTPQ_SHARD = os.environ["MTPQ_SHARD"]
OUT_RELEASE = os.environ["OUT_RELEASE"]
NEW_SHARD_NAME = "model-mtp-quantized.safetensors"

_DTYPE_BYTES = {
    "F64": 8, "F32": 4, "F16": 2, "BF16": 2,
    "I64": 8, "I32": 4, "I16": 2, "I8": 1, "U8": 1,
    "F8_E4M3": 1, "F8_E5M2": 1, "BOOL": 1,
}


def log(m):
    print(f"[merge_mtpq] {m}", flush=True)


def shard_tensor_bytes(path):
    """сумма байт тензоров в шарде + список ключей (читает только хедер)."""
    total = 0
    keys = []
    with safe_open(path, "pt") as f:
        for k in f.keys():
            sl = f.get_slice(k)
            shp = sl.get_shape()
            n = 1
            for s in shp:
                n *= s
            total += n * _DTYPE_BYTES[sl.get_dtype()]
            keys.append(k)
    return total, keys


def main():
    assert not os.path.exists(OUT_RELEASE), f"{OUT_RELEASE} уже существует — выбери новое имя"
    os.makedirs(OUT_RELEASE)

    # 1. hardlink всё из BASE кроме старого mtp-шарда, index.json, config.json
    base_files = os.listdir(BASE_RELEASE)
    old_mtp_shard = None
    for fn in base_files:
        src = os.path.join(BASE_RELEASE, fn)
        if not os.path.isfile(src):
            continue
        if fn in ("model.safetensors.index.json", "config.json", "CHECKSUMS.sha256"):
            continue
        if fn.startswith("model-mtp-"):     # старый grafted BF16 overlay — НЕ берём
            old_mtp_shard = fn
            continue
        if fn.endswith(".flat-broken.bak") or fn.endswith(".flat.bak"):
            continue
        dst = os.path.join(OUT_RELEASE, fn)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    log(f"hardlinked ствольные/токенайзер файлы (пропущен старый mtp overlay: {old_mtp_shard})")

    # 2. copy новый квантованный mtp-шард
    shutil.copy2(MTPQ_SHARD, os.path.join(OUT_RELEASE, NEW_SHARD_NAME))
    log(f"скопирован {NEW_SHARD_NAME}")

    # 3. index.json
    base_idx = json.load(open(os.path.join(BASE_RELEASE, "model.safetensors.index.json")))
    wm = base_idx["weight_map"]
    wm = {k: v for k, v in wm.items() if not k.startswith("mtp.")}   # убрать старые mtp
    new_bytes, new_keys = shard_tensor_bytes(os.path.join(OUT_RELEASE, NEW_SHARD_NAME))
    for k in new_keys:
        wm[k] = NEW_SHARD_NAME
    n_mtp = sum(1 for k in new_keys)

    # пересчёт total_size из хедеров всех уникальных шардов
    shards = sorted(set(wm.values()))
    total_size = 0
    for sh in shards:
        b, _ = shard_tensor_bytes(os.path.join(OUT_RELEASE, sh))
        total_size += b
    base_idx["weight_map"] = wm
    base_idx.setdefault("metadata", {})["total_size"] = total_size
    json.dump(base_idx, open(os.path.join(OUT_RELEASE, "model.safetensors.index.json"), "w"), indent=2)
    log(f"index.json: {len(wm)} ключей всего, {n_mtp} mtp.* -> {NEW_SHARD_NAME}, total_size={total_size}")

    # 4. config.json
    cfg = json.load(open(os.path.join(BASE_RELEASE, "config.json")))
    ig = cfg["quantization_config"]["ignore"]
    before = len(ig)
    ig = [x for x in ig if x != "re:.*mtp.*"]
    removed = before - len(ig)
    assert removed == 1, f"ожидали убрать ровно 1 're:.*mtp.*', убрали {removed}"
    if "re:.*mtp\\.fc" not in ig:
        ig.append("re:.*mtp\\.fc")
    cfg["quantization_config"]["ignore"] = ig
    json.dump(cfg, open(os.path.join(OUT_RELEASE, "config.json"), "w"), indent=2)
    log("config.json: 're:.*mtp.*' -> 're:.*mtp\\.fc' (эксперты/self_attn/shared_expert "
        "mtp теперь квантованы; нормы/gate/shared_expert_gate BF16 через generic-регэкспы ствола)")

    log(f"ГОТОВО. Новый релиз: {OUT_RELEASE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
