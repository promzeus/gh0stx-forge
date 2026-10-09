# Local training on gx10 — findings and open work

## AEON-27B A/B verdict (2026-09-08/09)

Head-to-head vs gh0stx-141B, same prompt, same sampling (temp 1.0/top_p 0.95/top_k 20): auth
code-review audit and an offensive security-assessment plan against an authorized target app
(white-box).

gh0stx-141B committed both times — 5 findings in 1423 tokens on the audit, a ranked attack-chain
plan in 1252 tokens on the offensive test, `finish=stop`, no hallucinated commands. AEON-27B did
not commit either time — 5000 tokens on the audit (`finish=length`, content never closed
`</think>`), 4000 tokens on the offensive test, same failure. The reasoning content itself hits
the right targets (same vulnerability classes, same attack surface) but never resolves into an
answer within the token budget.

This is not a data gap we introduced — AEON's own model card
(`AEON-7/Qwen3.8-27B-AEON-ULTIMATE-UNCENSORED-BF16`) documents it as a known, acknowledged
limitation of the abliteration trial they shipped (trial 48/50, KL ~0.04-0.10, chosen for
coherence over minimal KL): "Early Access Draft, not GA... on extremely long requests, small
gaps in the weights start to stack. A loop shows up. A phrase repeats. The thought does not know
how to land." An SFT patch can bias toward committing sooner; it doesn't remove the underlying
cause, which is baked into the weight edit itself.

DFlash2 speculative decoding gave no speedup either. `incoai/Qwen3.8-27B-DFlash2` is trained
against the clean `Qwen/Qwen3.8-27B` distribution; AEON is abliterated, so its output
distribution diverges from what the drafter predicts. Measured: mean acceptance length 1.5,
per-position acceptance 0.33/0.17/0.08/.../0.00 — the target rejects almost everything past the
first token. Plain (non-speculative) AEON ran 4.5-9.4 t/s; with DFlash2, 5.4-6.2 t/s — no gain,
sometimes worse. gh0stx holds ~19 t/s because its DFlash drafter was self-distilled directly off
gh0stx (matched distribution), not borrowed off-the-shelf.

Conclusion: AEON-27B does not replace gh0stx-141B on either quality or speed. It remains useful
as a QLoRA-trainable proof-of-concept (see below) and as a future lean-rebuild candidate
([[lean-model-approach]]), not as the primary model.

## QLoRA on gx10 works — for a dense model (2026-09-09)

Standard bitsandbytes 4-bit + peft LoRA on AEON-27B (dense, no MoE) loads and trains cleanly on
gx10: post-load GPU memory 17.7 GB, 496 adapted matrices (self_attn q/k/v/o on the 16
full-attention layers + linear_attn in/out projections on the 48 GDN layers + mlp gate/up/down on
all 64 layers — exact count confirms the regex), 116.7M trainable params, first forward+backward
loss 0.6082, all 917 SFT examples usable.

One real gotcha: the bnb 4-bit per-tensor load path (851 individual weight tensors for this
checkpoint) does not release each tensor's CPU-side bf16 source promptly after quantizing to
GPU — host RAM climbed steadily through the whole load (119G free → 9G free) instead of staying
flat, before eventually recovering once Python's GC caught up. Fix: a background thread calling
`gc.collect()` + `torch.cuda.empty_cache()` every 3s for the duration of `from_pretrained()`.
With the ticker, the same load bottoms out around 50G free (never dangerous) and completes in
~5.5 minutes. Without it, load time is similar but memory heads into OOM territory — killed
manually once at 9G free before it could crash the box.

Target module regex for this architecture (Qwen3.5-hybrid dense, `AEON-7/Qwen3.8-27B-...`):
```
model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj)
  |linear_attn\.(in_proj_qkv|in_proj_a|in_proj_b|in_proj_z|out_proj)
  |mlp\.(gate_proj|up_proj|down_proj))$
```
Note: no `language_model.` prefix in the live `named_modules()` tree, even though the safetensors
key convention on disk uses one — don't assume checkpoint key names match runtime module names,
verify with a `DUMP_MODULES` probe first.

SFT data used: `sec_sft.jsonl` + `hard_sft.jsonl` + `revshell_battery_sft.jsonl` merged (917
examples, median assistant length 344 chars) — already terse, already security-domain, already
matches the exact task class (audit/security-assessment) where AEON failed to commit. No new data
authored.

Training run was stopped before completing (GPU needed for the 141B investigation below) — the
harness works end to end; resuming is cheap (`train_aeon_qlora.py` on the box, drop
`FAILFAST_ONLY`).

## gh0stx-141B local training — root cause, corrected (2026-09-09)

Two box freezes this session chasing this. Full diagnosis below so a third attempt doesn't repeat
the cost.

**What's actually on disk.** Routed MoE experts in `gh0stx_rft_v5` are genuinely NVFP4-quantized
(`weight_packed` + `weight_scale` + `input_global_scale` + `weight_global_scale`, real
compressed-tensors format) — an earlier read of this session wrongly concluded they were plain
bf16 (~270GB); that number was the size of dense placeholder tensors a broken fallback path
synthesizes, not the real on-disk footprint. The actual per-expert quantized tensors are stored
individually: `model.language_model.layers.N.mlp.experts.{0..173}.{down,gate,up}_proj.weight_packed`
— 174 separate tensors per layer/projection, not one fused tensor.

**Why the generic loader fails.** `Qwen3_5MoeExperts` (the modeling class) declares ONE fused
`nn.Parameter` per layer/projection — e.g. `down_proj` of shape `(174, 4096, 1024)` — and expects
the checkpoint to provide that exact key. It never does; the checkpoint has 174 separate quantized
per-expert keys instead. `transformers.AutoModelForCausalLM.from_pretrained` can't reconcile the
two, marks the fused key "missing", and materializes it as a dense bf16 placeholder via
`torch.empty_like` — for all ~120 such keys across the model, `dense_total=262.73GB`. That
placeholder mass is what blows host RAM (121GB box), not the real quantized weights (vLLM serves
the same checkpoint at ~90GB GPU footprint using its own weight-loading code, which understands
the per-expert layout directly).

**Version-upgrade hypothesis, checked and rejected.** transformers 5.16.1 (up from 5.10.2) adds
generic per-expert-quantized→fused conversion machinery (`DecompressExperts`, `get_experts_scheme`,
`WeightConverter` in `transformers/integrations/compressed_tensors.py`) — real infrastructure for
exactly this problem, confirmed by reading the source. But `modeling_qwen3_5_moe.py` /
`modular_qwen3_5_moe.py` never declare a `WeightConverter`/`conversion_mapping` for their experts —
the mechanism exists in the library but isn't wired to this architecture. Confirmed via
`grep`, no live load attempt needed. Upgrading transformers alone will not fix this.

**Dequant mechanics verified (2026-09-09), cheap and safe.** Read one expert's
`weight_packed`/`weight_scale`/`weight_global_scale` via `safetensors.safe_open().get_tensor()`
(mmap-backed, no full-shard load) and run them through
`compressed_tensors.compressors.nvfp4.base.NVFP4PackedCompressor.decompress()` with a
`QuantizationScheme` built directly from `config.json`'s `quantization_config.config_groups.group_0`
— gives correct-shape bf16 output, no NaN/Inf, plausible weight magnitudes. Scaled to a whole
layer (174 experts × gate/up/down projections, layer 1 of `gh0stx_rft_v5`): 7.3s, 9GB peak RSS,
zero risk — this is the cheap validation step the freeze-avoidance rule above calls for, and it
passed. `NVFP4PackedCompressor.decompress()` only handles one 2D expert at a time (batching a
leading expert dim throws `ValueError: too many values to unpack` inside its `m, n = packed.shape`
— has to be called per-expert, then stacked after).

**Why this doesn't collapse into a quick fix.** `Qwen3_5MoeExperts.__init__` declares
`gate_up_proj`/`down_proj` as plain `nn.Parameter(torch.empty(num_experts, ...))` — not any
compressed-tensors-aware quantized-parameter class. That means the model class was never wired to
accept a stacked *quantized* per-expert tensor and dequantize it on the fly during forward (unlike
`self_attn`/`linear_attn`'s plain `nn.Linear` layers, which the generic compressed-tensors
integration CAN wrap automatically) — it wants the fully dequantized bf16 values already sitting
in that parameter. Two consequences:
- A checkpoint-conversion approach (stack quantized per-expert tensors under the fused key name,
  leave them quantized on disk) does NOT work here — nothing in the model's forward pass knows how
  to dequantize a batched (174, ...) `weight_packed` at runtime for this architecture.
- A full dequantized-on-disk conversion needs ~262GB (the same `dense_total` figure from the
  missing-key bug) for just the expert weights — doesn't fit in 171G free disk (checked
  2026-09-09: only ~6-12GB reasonably reclaimable from docker/HF caches, not remotely enough).

**What's left is a JIT streaming loader, and it's a real project, not an evening fix.** Register a
forward hook on each `Qwen3_5MoeExperts` instance that, right before that layer's forward call,
dequantizes its experts from the safetensors shards straight into `gate_up_proj.data`/`down_proj.data`,
then frees them right after. The naive version (dequantize all 174 experts every forward, every
layer) costs ~7.3s × (number of MoE layers) of pure I/O+dequant overhead added to EVERY forward
pass — at, very roughly, 30-45 MoE layers that's 3.5-5.5 minutes per forward, making a ~344-step
LoRA run take tens of hours. The practical version only dequantizes the experts actually selected
by top-k routing for the current batch (the same trick vLLM/DeepSpeed-MoE use) — far less data per
step, but requires hooking into the routing decision before the expert compute, not just before
the whole `Qwen3_5MoeExperts` module's forward. Not started. Scope this properly before picking it
back up — it's a small systems-engineering project, not a quick patch.

Since our LoRA target set (`self_attn`, `linear_attn`, `shared_expert`) never touches routed
experts, whatever loader gets built only needs a **forward** path (no gradient through expert
weights) — that part of the plan still holds.

**Routing mechanics read (2026-09-09), design now concrete.** From
`modeling_qwen3_5_moe.py`: `Qwen3_5MoeSparseMoeBlock.forward()` runs the router
(`Qwen3_5MoeTopKRouter`, a small always-resident `nn.Linear`-like weight, cheap) BEFORE calling
`self.experts(...)`, producing `selected_experts` (top-k indices per token). `Qwen3_5MoeExperts.forward()`
itself already reduces this to `expert_hit` — the small set of expert indices actually touched by
ANY token in the batch — via `expert_mask.sum(dim=(-1,-2)).nonzero()`, then loops only over those,
indexing `self.gate_up_proj[expert_idx]` / `self.down_proj[expert_idx]`. So the minimal-work set is
already computed by the model itself; the problem is only that `gate_up_proj`/`down_proj` are
declared as single `nn.Parameter(torch.empty(174, ...))` tensors, so today they get either fully
materialized (dense OOM) or not at all — there's no lazy/per-index path.

The fix is NOT "JIT-populate slices of the existing parameter" (that still requires allocating the
full (174, ...) tensor up front, defeating the purpose) — it's replacing `Qwen3_5MoeExperts`
entirely for the frozen inference path: construct the model with `gate_up_proj`/`down_proj` on the
`meta` device (zero real allocation) and monkeypatch `Qwen3_5MoeExperts.forward` to, instead of
indexing the (unmaterialized) parameter, call the per-expert dequant (already proven above) for
just the indices in `expert_hit`, run the linear ops against those on-the-fly tensors, and let them
get GC'd at the end of that forward call. Router weights, `shared_expert`, and everything LoRA
touches stay as normal materialized (bnb-quantized where applicable) parameters — only the routed
`Qwen3_5MoeExperts` instances need this treatment.

Open questions before implementing: how many unique experts actually get hit per training batch
(determines real per-step overhead — top_k routing among 174 experts for a short terse-SFT example
could plausibly hit only a few dozen, not all 174, which would cut the naive 7.3s/layer estimate a
lot); whether `meta`-device construction interacts cleanly with `prepare_model_for_kbit_training`
and gradient checkpointing on the surrounding (non-expert) modules. Not yet tested — next concrete
step is a small standalone reproduction (build one `Qwen3_5MoeSparseMoeBlock` in isolation, feed it
a real hidden-state batch, monkeypatch its experts forward, measure hit-count and wall time) before
wiring into a full model load.

## Box-freeze incidents (2026-09-09) — read before the next attempt

1. First attempt (`train_box_nvfp4.py`, old transformers 5.10.2): OOM-killer fired after the host
   climbed to anon-rss 108.5GB (dense placeholder materialization, see above). Box was
   SSH-unresponsive for ~13 minutes before the killer intervened and RAM recovered on its own.
2. Second attempt (`load_probe_141b.py`, transformers 5.16.1, testing the version-upgrade
   hypothesis live): box went SSH-unresponsive and did **not** recover — 41+ minutes, ~110 retry
   attempts, zero response. Required a remote power-cycle (user has smart-plug/IPMI access).
   Post-recovery: filesystem came back clean (EXT4 remounted rw without errors, no I/O errors in
   dmesg beyond expected unclean-shutdown journal warnings), no data loss, GPU visible again.
   The probe's own log file was empty — stdout never flushed before the freeze, so this attempt
   produced zero diagnostic signal; the version-upgrade hypothesis was rejected by static source
   analysis afterward, not by this failed live test.

**Rule going forward:** don't attempt to load `gh0stx_rft_v5` (or any checkpoint with this
per-expert-quantized MoE layout) via generic `AutoModelForCausalLM.from_pretrained` again. Build
and unit-test the per-layer streaming dequant loader against a *single* layer's tensors first
(cheap, no full-model memory risk) before ever wiring it into a full model load.

## Refs
- [[aeon-27b-ab-verdict]], [[local-training-cost-guard]] — memory records with the same findings,
  terser.
- `train_aeon_qlora.py`, `load_probe_141b.py` — scripts on gx10 (`/home/gh0stx/`).

## Requant path (C) confirmed, streaming loader (B) rejected — hit-count settles it (2026-09-09)

Block-level repro (`probe_moe_block_requant.py` on box, layer 1, no full-model load) answered the
open question that gated the loader choice: **how many unique experts get hit per batch**.
`top_k=10` among `num_experts=174` → hits ≈ all experts every batch. Measured: seq 64 → 158/174,
seq 256 → 174/174, seq 512 → 174/174. The per-hit selective-dequant trick the JIT-streaming loader
(B) relied on to cut overhead is therefore useless here — B pays the full ~7.3s/layer python
`decompress()` on every forward × 60 layers → tens of hours/step. **B rejected on data.**

Requant path (C) — resident nf4 on GPU, bnb fused dequant — measured on the same block:
- Forward (dequant+linear) 54-60ms/layer vs bf16-dense 37ms → ~3.6s/forward across 60 layers. OK.
- nf4 resident 1.09GB/layer × 60 ≈ 65GB routed experts (matches the ~84GB NVFP4 serve minus trunk).
- Load-time requant 119s/layer python-side → ~2h for full model → MUST cache nf4 to disk once,
  reload fast (converter `build_nf4_cache.py`, per-layer files under `/home/gh0stx/nf4_cache/`).
- Open risk = quality: block-output relFro 12.3%, cos 0.992 (per-expert 9% errors sum across the
  174). Compounds over 60 layers unknown → end-to-end A/B on the two commit prompts is the gate,
  micro-metrics don't substitute.

Loader design (clean split matching `adapter-final/` target set): routed experts → frozen custom
`RequantExperts` (per-expert nf4 packed + monkeypatched forward, gate/up kept separate as on disk,
NOT LoRA-targeted); shared_expert + self_attn q/k/v/o + linear_attn → bnb `Linear4bit` so peft
attaches LoRA. Router `gate.weight`, norms, embeddings → bf16. Build on `meta` device, swap expert
modules, stream weights in per-layer — never let generic `from_pretrained` materialize the fused
(174,…) dense expert param (that is the 262GB freeze).

## Path-C QLoRA proven on the quantized model (partial depth, 2026-09-09)

Full train contract validated end-to-end on a 2-layer `Qwen3_5MoeTextModel` (scripts on box:
`requant_qlora.py`, module `RequantExperts`): nf4 routed experts loaded from the disk cache +
trunk target linears (linear_attn in/out projections, shared_expert, self_attn q/k/v/o) quantized
to bnb `Linear4bit` + LoRA (r16/α32) on the `adapter-final` target set + `PagedAdamW8bit`. One
step: grads present 32/32 (mean|g| 2e-7), `opt.step()` moved lora_B (max|Δ| 1.9e-4), gradient
checkpointing compatible. Forward+backward+optimizer all work — the engine trains.

Gotchas hit and resolved:
- `RequantExperts` integrates into the real `Qwen3_5MoeSparseMoeBlock.forward` unchanged (gate/up
  kept separate as on disk); frozen experts don't break the graph — a base layer weight still gets
  grad (verified 4e-8), grad reaches attention/LoRA through the residual stream.
- LoRA-grad "0/32" was a test artifact: grads were checked after `opt.zero_grad()`. `autograd.grad`
  confirmed lora_B in the graph before that was found. `disable_adapter` diff = 0 at init is
  expected (lora_B initialized to zero), not a wiring failure.
- `gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})` — the
  kwarg is nested, not direct. `use_cache=False` required in the forward call (linear-attention-only
  truncation trips the Cache seq-length path otherwise).

Memory (measured @2L, extrapolated): routed experts nf4 1.095GB/layer × 60 = 66GB + trunk nf4
(~0.1GB/layer) + embed/lm_head/norms fixed ~2GB + forward/backward peak overhead ~5GB ≈ 75-80GB /
122GB. Fits with headroom; the trunk MUST be nf4 (bf16 trunk pushes resident to ~110GB, too tight).

Not yet done: full 60-layer load needs `meta`-device construction (CPU-construct is 35GB/layer,
doesn't scale) with trunk materialized from safetensors via `load_state_dict(assign=True)` and
rotary buffers re-inited (meta buffers aren't in the checkpoint) — the CPU-construct partial path
sidesteps this but the full run needs it. Then serve + A/B on the two commit prompts (the 12%
block-output error is the open quality risk). nf4 cache build in progress (~120s/layer, ~2h total).

## Meta-device scalable loader works (2026-09-09)

`requant_full.py` on box: `Qwen3_5MoeTextModel` on `meta` (zero alloc), swap experts for
nf4-from-cache `RequantExperts`, assign trunk bf16 from safetensors (prefix `model.language_model.`
stripped to TextModel keys; shared_expert NVFP4→bf16 via decompress), separate lm_head. Result at
N=4: trunk assign missing=0/unexpected=0/meta_params=0, forward finite, logits (1,256,248320),
9.78GB. Scales to 60L (meta construct never allocates the 262GB dense expert param — the freeze).

Two gotchas:
- The checkpoint class `Qwen3_5MoeForConditionalGeneration` is multimodal — carries a full
  `model.visual.*` vision tower (333 params) the text checkpoint never fills, so `.to(cuda)` chokes
  on meta vision params. Use `Qwen3_5MoeTextModel` + a manual lm_head for training; serve/A-B go
  through vLLM, not this HF path.
- Rotary buffers (`rotary_emb.inv_freq`, `original_inv_freq`) aren't in the checkpoint and stay meta
  after assign. `ROPE_INIT_FUNCTIONS["default"]` KeyErrors in this transformers; recompute by
  constructing a fresh `type(mod)(text_config)` on device and copying its `inv_freq`.

Engine status: every mechanism proven (nf4 requant floor, hit-count kills B, RequantExperts forward,
QLoRA grads+optimizer step, meta scalable load). Remaining to a real run: (1) nf4 cache finishes,
(2) assemble meta-loader + trunk `Linear4bit` + LoRA + SFT loop into one script, (3) full-60L memory
confirm, (4) serve trained adapter via vLLM + A/B on the two commit prompts (12% block-error is the
open quality risk).

## Full-corpus run too slow — measured, focused instead (2026-09-09)

Path-C full run (`train_141b_requant.py`, 1333 ex × 2 ep) measured >34s/example: 45 min compute
(GPU 96%, CPU-bound, no stall) with zero step-10 lines (80 examples). Extrapolates to 25+h — not
viable on a box that froze twice. FAILFAST hid this: it timed one example inside the load window.

Cost breakdown: 48/60 layers are linear-attention on the fla torch-fallback (sequential GDN
recurrence, no fla/causal-conv1d), plus `RequantExperts` dequantizes ~174 experts × 3 proj × 60
layers nf4→bf16 in a Python loop every forward, and gradient checkpointing recomputes that forward
on backward (×2). The per-forward expert re-dequant is the dominant term, unavoidable while experts
stay nf4 (holding them bf16 needs ~130GB, doesn't fit). Disabling checkpointing halves it but blows
past 128GB. Fixing fla (nvcc + version-matched fla) speeds only the attention third, big effort.

Decision: focused run instead — `sft_commit_focus.jsonl` (501: AT×2 166 + revshell 163 + aircrack×2
52 + sec 80 + hard 40, batteries 76%), 1 epoch, SAVE_STEPS=15 → ~2-2.5h. Tests the commit fix (the
batteries) fast; the full-corpus round waits on a real speedup (fla fast-path, or a bf16-expert
tier that fits, or fewer checkpointed layers).
