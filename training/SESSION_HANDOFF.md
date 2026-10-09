# Session handoff — 2026-09-10

Path C (train quantized 141B locally) proven end-to-end and running. Full technical detail in
`LOCAL_TRAINING.md`; commit-format rules in `COMMIT-FORMAT.md`; this file = live state.

## Live state on gx10

Focused commit-fix run in progress: `train_141b_requant.py`, DATA=`sft_commit_focus.jsonl` (501:
AT×2 166 + revshell 163 + aircrack×2 52 + sec 80 + hard 40), 1 epoch, log
`/home/gh0stx/train_141b_focus.log`, out `/home/gh0stx/gh0stx141_focus_out/`. As of snapshot: step
50/61, loss 0.5486 (step10) → 0.1239 (step50), converging. Checkpoints written: step_15, step_30,
step_45 (valid PEFT adapters: adapter_config.json + adapter_model.safetensors). ~2h to epoch_0+DONE.
Pace ~80s/example (fla torch-fallback + per-forward nf4 expert dequant ×2 from grad-checkpointing) —
full 60L run is ~11h, the reason the full 1333×2 corpus was abandoned for this focused 501×1.

Monitor watching for DONE/epoch_0/crash. Box survived ~11h, peak 99.1GB / 128 (chunked CE + MAXLEN
8192), swap 83GB. RAM steady ~13GB avail during the run.

## Next: A/B the trained adapter (the whole point)

Once DONE (or to test step_45 now): stop training to free GPU, then check whether `finish=stop` +
committed content after `</think>` returns on the two prompts (`audit_msgs.json`,
`offensive_msgs.json`), the AT non-commit failure this round targets.

Serving the adapter — RESOLVED 2026-09-14, see "Full-corpus round DONE + served-LoRA A/B" below.
vLLM `--enable-lora --lora-modules <name>=<adapter>` works and DOES apply to the `linear_attn`
(GatedDeltaNet) modules; the "UNTESTED / may silently skip GDN" worry was unfounded. The slow
requant-loader fallback and off-box merge are no longer needed for eval.

Eval command shape (once served on :8000): `eval_run.py <tokdir> gh0stx <msgs.json> <out>` —
add_generation_prompt appends `<think>`, /v1/completions returns raw text + finish_reason. finish=stop
= committed; finish=length = non-commit persists.

## What's proven / done this session

- Requant path C: NVFP4→nf4 second-quant adds only nf4's own floor (no compounding); block hit-count
  = all 174 experts/batch (killed the streaming-loader B); RequantExperts forward; QLoRA grads+step;
  meta-load 60L scalable; nf4 cache (60 layers, 64GB, `/home/gh0stx/nf4_cache/`, DONE).
- Commit-format fix: batteries reformatted `<think>{decision}</think>{committed}`, validated through
  the train render path (0 empty commits, 0 value leaks). Scripts on box: `reformat_commit_battery.py`,
  `validate_trainpath.py`, `assemble_focus.py`. Harvested sec/hard already commit (multi-turn ReAct).
- Rules doc `training/COMMIT-FORMAT.md`; memory `[[commit-format-fix]]`.

## Open, not done

- A/B verdict (above) is the deliverable.
- Speed: ~80s/example makes any real corpus round impractical. Needs fla fast-path (nvcc + version-
  matched fla) or vectorized per-expert nf4 dequant (the Python 174-expert loop over 60 layers, twice, is the
  bottleneck) before a full-corpus round.
- gh0stx v5 serving stays down while training holds the GPU.


## A/B eval finding (2026-09-10): requant forward is correct, KV-cache decode was buggy

First A/B via requant-loader free generation produced pure gibberish (random CJK + fragments) on
BOTH base and adapter. Not a training failure. Single-forward diagnostic (`diag_logits.py`) settled
it: top-12 next tokens after the AT prompt's `<think>\n` are sensible Russian word-starts (П, За,
Ком, Это, Про, По, Ин; max logit 20.88, finite). The forward and logits are correct, so the nf4
double-quant did NOT break inference (resolves the earlier "12% block-error" worry for a single
step). The garbage came from the incremental KV-cache decode: first token from the full-prompt
forward is fine, tokens 2+ via `past_key_values` diverge (likely missing cache_position -> rotary at
pos 0 for the new token). Bug is in the eval decode loop, not the model.

Valid verdict pending via no-cache generation (`eval_commit_ab.py`, MAXNEW=140, ~16min/gen): each
token re-forwards the full context (proven-correct forward), slow but correct. For fast future evals,
either fix cache_position in the incremental path, or serve the adapter through vLLM (base generates
coherently there).

## A/B VERDICT (2026-09-10): training worked

No-cache A/B (correct forward), AT IMEI prompt, epoch_0 adapter vs base:
- base: reasons, closes </think>, commits bare `` `AT+CGSN` `` (19 chars, finish=stop).
- adapter: reasons in the trained decision style ("Спрашивают:… Команду знаю, значение не выдумываю,
  беру реальный ответ модуля"), closes </think>, commits the full battery answer with the real module
  transcript (`AT+CGSN` / 352931108990482 / OK + interpretation). finish=length = the 140-token budget
  cut it mid-answer, not non-commit (</think> closed, committing).

Training imprinted the commit-format verbatim (reasoning + structured answer from
`reformat_commit_battery.py`). Path C validated end-to-end: quantized 141B trained locally, LoRA
changed behavior, confirmed by A/B. Base also commits the bare command on this easy prompt with a
correct decoder, so the total non-commit did not reproduce here — the original failure was on the
serving path (qwen3 reasoning-parser) and longer cases; the adapter effect is still unmistakable.

Next: full-corpus round (1333, more epochs) — resume logic in `train_141b_requant.py` (RESUME=1).
Fix cache_position in the incremental decode for fast eval, or serve the adapter via vLLM.

## Gathering instruction (2026-09-10)

`DATA-GATHERING.md` written: how the local model produces more offsec SFT in commit-format (task shape, harvest loop, coverage ranked by OFFSEC-GAPS, validation gate). ~400 authored tasks across ~17 domains are the fastest broadening — harvest + commit-format them before authoring new domains; top mission gaps (pivot/AD, cloud-identity, crypto) get new deterministic batteries.

## Full-corpus round DONE + served-LoRA A/B (2026-09-14)

`train_141b_requant.py` defaults (DATA=`sft_commit_v1.jsonl`, 1333 usable, EPOCHS=2) finished:
666 steps / 4103 min (~68h), final loss ~0.03-0.09, adapter `/home/gh0stx/gh0stx141_lora_out/epoch_1`.
Pace ~12 min/step, box stable throughout (load ~1.05, RAM 109/121G, swap untouched). Run required
stopping the `vllm-dflash` docker container first to free the ~115G it held for serving.

**Served-path A/B (the fast, correct way — replaces the slow no-cache loader):** deploy the adapter
through vLLM itself with `--enable-lora`. `dflash_deploy_lora_test.sh` on box = `dflash_deploy.sh`
plus a third mount `-v <adapter>:/adapter:ro` and flags
`--enable-lora --max-loras 1 --max-lora-rank 16 --lora-modules gh0stx-full=/adapter`. Call the
adapter by its lora name (`"model": "gh0stx-full"`) at `/v1/chat/completions`.

Result, both canonical prompts, temp 1.0/top_p 0.95/top_k 20:
- audit → finish=stop, 1446 tok, real findings (file:line + severity + exploit + fix), no collapse.
- offensive → finish=stop, 1777 tok, structured attack plan with concrete file:line refs.
Non-commit did NOT reproduce. The earlier no-cache "non-commit" reads were test artifacts:
MAXNEW=280 (too small; 141B needs ~1250-1450 tok on these) + grader searched for a literal
`</think>` that `--reasoning-parser qwen3` strips from `content`.

**Infra correction:** vLLM DOES apply LoRA to the Qwen3.5-MoE GatedDeltaNet layers. The adapter
targets `in_proj_{qkv,a,b,z}` + `out_proj` (GDN) alongside q/k/v/o and gate/up/down, and vLLM
(`vllm/model_executor/models/qwen3_5.py`: `Qwen3_5ForCausalLMBase(SupportsLoRA)`,
`packed_modules_mapping` includes `in_proj_qkvz`/`in_proj_ba`) loaded and served it with a visible
behavior change. The `eval_commit_ab.py` docstring claim that vLLM "cannot LoRA the GatedDeltaNet
layers" is wrong — the whole slow no-cache loader detour was built on that false premise.

**Two gotchas when adding LoRA to the deploy:**
- KV cache squeeze: adapter + rank-16 buffers eat GPU memory, so 90k ctx no longer fits at
  `--gpu-memory-utilization 0.88` (vLLM aborts: needs 10.11 GiB KV, has 5.43). Fix used: CTX=32768 +
  util 0.92. A production LoRA serve needs either lower ctx or higher util, not the 90k-pure config.
- Detach test runs with `nohup ... </dev/null & disown`; a plain `&` over ssh dies by SIGHUP when
  the ssh call returns (lost the first `test_lora_serving2.py` run to exactly this).

**Not done:** base-vs-adapter side-by-side on the served path. Base gh0stx-141B already commits these
two prompts ([[aeon-27b-ab-verdict]]), so this run proves "adapter serves correctly with LoRA, no
non-commit, no collapse" — not "strictly better than base." A clean delta needs base through the same
endpoint (serve without `--lora-modules`, or request `"model": "gh0stx"`).
