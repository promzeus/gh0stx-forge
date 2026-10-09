# Training approach — lean recipe (how AEON/z-lab do it, applied to us)

Our current pipeline (prune 397B→141B + multiple SFT rounds + attempted DPO) is heavy and
degrades coherence — the IMEI non-commit is a symptom of over-processing. AEON's models are
NOT fine-tuned; they weight-edit a strong base and select against capability loss. Adopt that.

## What AEON actually does (no fine-tuning)
Abliteration = directional ablation (Arditi 2024, refusal is a single direction). Orthogonalize
attention/MLP weights against the refusal direction — remove refusals, instill nothing (the base
already knows). Tool: `abliterix` (fork of `p-e-w/heretic`), Mamba/hybrid support.
- Pipeline: extract activations on harmful/benign prompts → refusal direction (mean-diff / PCA /
  SRA / SAE / OT) → candidate edits on attn/MLP/MoE (reversible LoRA or projection) → measure
  refusals + KL-divergence vs original → Optuna-TPE search over layers/strengths/decay/MoE.
- Datasets: `wangzhang/abliterix-datasets` `harmful_500` / `good_500` (SHA-pinned, reproducible).
- KEY philosophy: pick the "coherent unlock", NOT lowest-KL. Over-editing kills coherence, same as
  over-training. Judge (gemini-flash) scores the answer AFTER `</think>` → keeps CoT commitment.
- Runs locally: `--model.quant-method bnb_4bit`, native NVFP4 repack (`abliterix-abliterate-fp4`),
  auto-detects MPS/CUDA. No big training corpus needed.

## DFlash (speed, not training)
Block-diffusion drafter, single forward pass. 6x lossless, up to 2.5x over EAGLE-3 (arXiv
2602.06036). For Qwen3.8-27B the drafter exists off-the-shelf (`z-lab/Qwen3.8-27B-DFlash2`, 2B);
no need to train our own. Serve via `aeon-vllm-ultimate` (see `deploy/aeon-27b-dflash.md`).

## Our recipe (do it right)
1. Base: strong recent open dense-27B (Qwen3.8-27B / AEON abliterated 27B). Dense-27B ≈ our
   pruned-141B on per-token reasoning, and it is locally QLoRA-trainable. No pruning.
2. Uncensor: AEON weights are ALREADY abliterated (ULTIMATE-UNCENSORED) — with AEON as base,
   SKIP abliteration. Run `abliterix` ourselves only for a clean Qwen3.8-27B, to abliterate
   gh0stx-141B in place, or to re-abliterate AEON if it's not uncensored enough for offensive
   tasks (verify on the A/B). Configs: `abliterix/`.
3. Domain knowledge: minimal SFT ONLY where the base genuinely lacks it (security corpus, AT
   battery). Targets in the SAME terse format we query at inference (`Q → \`AT+CGSN\``, not verbose
   command+output+explanation). Verified per-command coverage (CEER etc.). Adequate weight — do not
   dilute a small battery in a 30k off-topic corpus. See [[sft-format-coverage-lesson]].
4. Sampling: temp 1.0 / top_p 0.95 / top_k 20 always. See `SAMPLING.md`.
5. Selection: if we tune, gate on a judge scoring post-`</think>` coherence (AEON's method), keep
   the variant that stays committed, not the most-edited one.

## Mistakes to avoid (from gh0stx v1-v5)
- Heavy prune (397→141 shed capacity we can't recover).
- Verbose SFT targets that mismatch the terse inference ask.
- Small skills diluted in a big off-topic corpus.
- Greedy sampling (temp 0) — drives loops/non-commit.
- Blind DPO/ORPO rounds before checking sampling + data format.

## Refs
- Arditi et al. 2024, Refusal is Mediated by a Single Direction — arXiv 2406.11717
- DFlash — arXiv 2602.06036
- github.com/wuwangzhang1216/abliterix · github.com/p-e-w/heretic · github.com/z-lab/dflash
