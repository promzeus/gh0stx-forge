# abliterix — uncensor by weight-editing (no fine-tune)

Concrete run configs distilled from the abliterix repo (`configs/default.toml`,
`configs/qwen3.5_122b_4bit.toml`). Runs locally on GB10.

## Install + run
```
pip install -U abliterix
# copy the chosen config to abliterix.toml in the working dir, then:
abliterix --config abliterix.toml --non-interactive --non-interactive-output-dir ./out
# NVFP4 repack (no bf16 blow-up) for vLLM serving:
abliterix-abliterate-fp4 ...
# exact replay of the winning trial:
abliterix --reproduce out/reproduce.json
```
Configs: `qwen38-27b.toml` (dense-27B, the lean base) · `gh0stx-141b-moe.toml`
(abliterate our 141B in place). Datasets `good_1000`/`harmful_1000` from
`wangzhang/abliterix-datasets`.

## The knobs that matter (capability vs uncensor)
- `steering.orthogonal_projection = true` — subtract ONLY the safety direction, keep the
  benign one. The single most important knob against reasoning degradation.
- `steering.vector_method` — `sra` (Surgical Refusal Ablation, concept-guided spectral
  cleaning) preserves capability best on dense; MoE uses tested `mean`.
- `kl.target = 0.01` — KL floor; below it the refusal objective dominates. This is how you
  get the "coherent unlock" instead of over-editing to min-KL (which kills coherence).
- `detection.llm_judge = true` (gemini-flash) — scores the answer AFTER `</think>`, so the
  pick keeps chain-of-thought commitment. Needs `OPENROUTER_API_KEY`; makes the run
  non-reproducible (drop it for a reproducible benchmark, keep it for the final pick).
- `optimization.num_trials` — more trials, better unlock. Dense 100; MoE 25 (expensive).
- `steering.strength_range` (MoE) — wider `[1.0, 10.0]`; dense uses the narrower default.
- `kl.prune_threshold = 5.0` — skips compliance eval on high-KL trials, speeds the search.

Deterministic fallback if no judge: `detection.compliance_markers` (substring list:
sorry / i cannot / as an ai / … + Chinese) — reproducible, no API key.

## Where this fits
Lean approach ([[lean-model-approach]], `../APPROACH.md`): abliterate a strong base instead
of prune + multi-SFT. Domain knowledge stays a minimal terse SFT on top, only where the base
lacks it.
