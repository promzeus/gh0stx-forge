#!/usr/bin/env python3
"""
vllm_hs_extractor_mtp.py — снять hidden-states для self-distill грефтнутой Qwen3.5 MTP-головы gh0stx.

ОТЛИЧИЕ ОТ vllm_hs_extractor.py (который был для EAGLE3/DFlash, multi-layer PRE-norm residual):
  MTP-голова на serve получает в аргумент `hidden_states` РОВНО ОДИН тензор =
  выход финального RMSNorm таргета (POST-final-norm), тот же самый, что идёт в lm_head.
  Доказательство (vLLM в контейнере vllm-mtp-v4):
    * v1/worker/gpu/model_runner.py:578  spec_hidden_states = hidden_states
      (ветка get_mtp_target_hidden_states НЕ срабатывает: у qwen3_5 нет hc_mult / нет метода)
    * model.forward -> Qwen3NextModel.forward: qwen3_next.py:541  hidden_states,_ = self.norm(hidden_states, residual); return hidden_states
      => возвращается POST-final-norm.
    * propose(last_hidden_states=spec_hidden_states); MTPSpeculator.model_returns_tuple=False
      => этот единственный тензор кладётся в self.hidden_states и идёт в mtp.forward(hidden_states=...).
  Значит для train==serve ловим ВЫХОД model.norm (out[0]), НЕ out[0]+out[1] слоёв.

ВЫРАВНИВАНИЕ (deriv. из autoregressive/speculator.py::_prepare_prefill_inputs_kernel):
  На serve буфер hidden[j] = h_j (POST-norm таргета на позиции j), а draft_input_ids[j] = x_{j+1}
  (сдвиг входных id на +1). MTP на позиции j фьюзит (embed(x_{j+1}), h_j) и предсказывает x_{j+2}.
  => ЭКСТРАКТОР сохраняет ИНДЕКС-ВЫРОВНЕННО: hidden_states[j] <-> input_ids[j] <-> loss_mask[j].
     Сдвиги (+1 для токен-входа, +2 для метки/лосс-маски) делает ТРЕНЕР, не экстрактор.

ENV: MODEL_DIR (=/home/gh0stx/nvfp4_v4, тот же чекпойнт что serve), OUT_DIR, DATA_JSONL,
     CHAT_TEMPLATE=qwen3.5, MAX_LEN=2048, MAX_SAMPLES=optional.
     Квантизация/dtype/флаги ДОЛЖНЫ совпадать с serve: compressed-tensors, bf16, language_model_only.
Запускать из specforge-ready с VLLM_ENABLE_V1_MULTIPROCESSING=0 (движок in-process -> хуки видят модуль).
"""
import os, hashlib, torch
import torch.nn as nn


def log(m): print(f"[extract-mtp] {m}", flush=True)


def find_layers(root):
    """Самый длинный ModuleList = декодер-слои таргета."""
    best = None
    for name, mod in root.named_modules():
        if isinstance(mod, nn.ModuleList) and len(mod) >= 40:
            if best is None or len(mod) > len(best[1]):
                best = (name, mod)
    return best


def find_final_norm(root, layers_name):
    """Финальный RMSNorm = сосед '.layers' в том же модуле (parent.norm).
    parent = <layers_name без '.layers'>; из него берём .norm."""
    parent_name = layers_name.rsplit(".layers", 1)[0]
    parent = root.get_submodule(parent_name) if parent_name else root
    norm = getattr(parent, "norm", None)
    assert norm is not None, f"нет .norm у '{parent_name}' (модули: {[n for n,_ in parent.named_children()]})"
    return f"{parent_name}.norm", norm


def main():
    MODEL_DIR = os.environ.get("MODEL_DIR", "/model")
    OUT_DIR = os.environ["OUT_DIR"]; os.makedirs(OUT_DIR, exist_ok=True)
    DATA = os.environ["DATA_JSONL"]
    CHAT = os.environ.get("CHAT_TEMPLATE", "qwen3.5")
    MAX_LEN = int(os.environ.get("MAX_LEN", "2048"))
    MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "0")) or None

    from vllm import LLM, SamplingParams
    from vllm.inputs import TokensPrompt
    from transformers import AutoTokenizer
    from datasets import load_dataset
    from specforge.data import build_eagle3_dataset

    tok = AutoTokenizer.from_pretrained(MODEL_DIR, trust_remote_code=True)
    ds = load_dataset("json", data_files=DATA)["train"]
    if MAX_SAMPLES: ds = ds.select(range(min(MAX_SAMPLES, len(ds))))
    eds = build_eagle3_dataset(dataset=ds, tokenizer=tok, chat_template=CHAT,
                               max_length=MAX_LEN, is_preformatted=False)
    log(f"датасет (build_eagle3): {len(eds)} сэмплов, колонки {eds.column_names}")

    # ВАЖНО: грузим ТОЧНО как serve (compressed-tensors NVFP4, bf16, language_model_only),
    # иначе train/serve skew в самих hidden. mtp.* пропускается загрузчиком (skip_prefixes=['mtp.']).
    log(f"init vLLM {MODEL_DIR} (nvfp4/compressed-tensors, eager, language_model_only)...")
    llm = LLM(model=MODEL_DIR, quantization="compressed-tensors", dtype="bfloat16",
              enforce_eager=True, gpu_memory_utilization=0.90, max_model_len=MAX_LEN + 128,
              trust_remote_code=True, enable_prefix_caching=False, language_model_only=True,
              load_format="runai_streamer")
    runner = llm.llm_engine.model_executor.driver_worker.model_runner
    found = find_layers(runner.model)
    assert found, "не нашёл декодер-слои"
    lname, layers = found
    nname, final_norm = find_final_norm(runner.model, lname)
    log(f"декодер-слои: '{lname}' ({len(layers)}); финальный norm: '{nname}' -> хук на POST-norm выход")

    cap = {}
    def norm_hook(mod, inp, out):
        # GemmaRMSNorm.forward(x, residual) -> (normed, residual); serve возвращает normed (out[0]).
        # НЕ складываем out[0]+out[1]: out[1] это residual, а нам нужен ровно POST-norm normed.
        h = out[0] if isinstance(out, tuple) else out
        cap["h"] = h.detach().clone()   # clone: защита от in-place fused_add_rms_norm
    handle = final_norm.register_forward_hook(norm_hook)

    sp = SamplingParams(max_tokens=1, temperature=0)
    n = 0
    def _flat(t):
        if hasattr(t, "dim") and t.dim() == 2:
            t = t.squeeze(0)          # build_eagle3 хранит [1, seq] -> [seq]
        return t.tolist() if hasattr(t, "tolist") else list(t)
    for ex in eds:
        try:
            ids = _flat(ex["input_ids"])
            lm = _flat(ex["loss_mask"])
            if len(ids) < 8: continue
            key = hashlib.md5(",".join(map(str, ids)).encode()).hexdigest()
            fpath = os.path.join(OUT_DIR, key + ".pt")
            if os.path.exists(fpath):  # resume
                n += 1; continue
            cap.clear()
            llm.generate(TokensPrompt(prompt_token_ids=ids), sp, use_tqdm=False)
            hs = cap["h"][:len(ids)].float().cpu()      # [S, H], H=4096, ЕДИНСТВЕННЫЙ блок (POST-norm)
            if hs.shape[0] != len(ids):
                log(f"skip (len {hs.shape[0]}!={len(ids)})"); continue
            torch.save({"input_ids": torch.tensor(ids, dtype=torch.long),
                        "loss_mask": torch.tensor(lm, dtype=torch.long),
                        "hidden_states": hs.to(torch.bfloat16)}, fpath)  # индекс-выровнено с input_ids
            n += 1
            if n <= 3 or n % 200 == 0: log(f"{n} ok, hs {tuple(hs.shape)}")
        except Exception as e:
            log(f"skip sample ({type(e).__name__}: {str(e)[:80]})"); continue
    handle.remove()
    log(f"DONE: {n} сэмплов -> {OUT_DIR} (hidden_states = POST-final-norm, [S,4096], index-aligned)")


if __name__ == "__main__":
    main()
