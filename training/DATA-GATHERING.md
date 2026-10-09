# Data gathering for the local model

Instruction for producing offsec SFT data locally, in commit-format, across the mission domains.
The local uncensored model (`gh0stx`) generates the security content; the harvester grades it, keeps
PASS trajectories, and reformats them to commit-format. Authorization and scope are the project's
(authorized internal offensive-security tooling against owned infra, everything local, model never
published) — see README "Жёсткие правила дизайна" and the mission memory.

Role split, fixed: the local model only gathers data and appends it, in commit-format, to the pool
file. Training is a separate step run against whatever the pool holds — validate, assemble, train.
The model produces the commands, payloads, and reasoning at harvest time under its operating rules;
the grader verifies each one by execution; the trainer consumes the validated pool and does not
author content. Coverage targets and per-domain detail live in `OFFSEC-GAPS.md` and
`OFFSEC-COVERAGE-MAP.md` — this file does not restate them, it drives the gathering.

## Pool file (the handoff)

The gathering appends to one JSONL: `~/octest/night/dataset/sft_commit_pool.jsonl` on the box. One
example per line, `{"messages":[{role,content}...], "meta":{...}}`, every assistant target already in
commit-format (`<think>{decision}</think>\n\n{committed answer}`). New PASS trajectories append here;
nothing overwrites. The trainer's only inputs are this pool and its per-source weights.

Train step (run against the pool, not by the model): `validate_trainpath.py` drops any line whose
final assistant turn does not commit non-empty or leaks a value into `<think>`; `assemble_*` applies
dedup and per-source weights; `train_141b_requant.py` (`RESUME=1`, `SAVE_STEPS`) trains. The engine
is proven (A/B in `SESSION_HANDOFF.md`), so the only variable is what the pool accumulates.

## Target format

Every gathered example lands in commit-format, the contract validated in `COMMIT-FORMAT.md`:

```
<think>
{decision: which command/technique and why, confusable wrongs, verify-not-recall}
</think>

{committed answer: the real command/payload/output the operator pastes, plus interpretation}
```

The decision goes in `<think>`; the runnable artifact and any captured value go after `</think>`.
No answer value (the exact bytes, the number, the payload line) inside `<think>` — that re-teaches
the non-commit failure. Inference runs thinking-mode (chat_template appends `<think>`, serve uses
`--reasoning-parser qwen3`), so a target without a committed answer after `</think>` serves empty.

## Task shape (gradeable)

A gathered item is a self-checking task, same shape the harvest pipeline already uses (`README.md` шаг 1):

```
id, category, behavior,
setup_sh              # builds fixtures in $W (task working dir)
prompt                # what the model answers; answer written to $W/RESULT.txt
grader_sh             # PASS/FAIL: computes expected from fixtures, compares to RESULT.txt
reference_solution_sh # setup -> reference -> grader must PASS by construction
self_test_passed      # true only after a real setup->reference->grader run
```

`self_test_passed` is set by running `setup_sh → reference_solution_sh → grader_sh`, never by eye.
Self-test in an underscore-pathed dir (`au_<tid>_<pid>_<rand>`) — tasks that `split("_")` break in a
clean `/tmp`. The grader runs the model's output (jq vs paste, payload vs lab, aws/kubectl vs
throwaway) and grades to line/byte accuracy — a grader that returns yes/no on prose is not enough
(`OFFSEC-GAPS.md` §2).

## Harvest loop

1. Author or generate a task bank for a domain (`harness/gen_*.py`, `harness/batteries/*.py`, or
   `harness/tasks/*.json`). ~400 tasks already authored across ~17 domains, all `self_test_passed`.
2. Serve `gh0stx` (`dflash_deploy.sh`), run the harvest loop (`harness/run_authored.sh`) — the model
   attempts each task under its operating rules, rejection-sampling: only PASS trajectories are kept.
3. Export the PASS sessions to SFT (`harness/export_sft.py`): opencode session → messages jsonl.
4. Reformat to commit-format. Deterministic batteries: extend the `*_battery_gen.py` generator to
   emit `<think>{decision}</think>{committed}` directly, or run `reformat_commit_battery.py` on the
   captured battery. Harvested multi-turn ReAct already commits its final turn (verified) — no
   reformat.
5. Validate through the real train render path (`validate_trainpath.py`): every example renders,
   the last assistant turn commits non-empty, no captured value leaks into `<think>`. A failing
   example reproduces the non-commit and is dropped or fixed.
6. Assemble with weights (`assemble_focus.py` / `assemble_commit_corpus.py`), then train
   (`train_141b_requant.py`, `RESUME=1` for multi-day).

## Coverage, ranked

Gather by mission impact, not by what is easy. `OFFSEC-GAPS.md` ranks it: network pivot + AD/Kerberos
lateral (G4-G6) first — dead center of the mission, near-zero current coverage; then recon-aggregation
+ CIDR/IP-math (G1-G2, our main input); cloud identity-pivot (G7); crypto attacks (G8); byte-exact web
(G3); zero-coverage domains (G9: mobile, RF/wireless, hardware/firmware, ICS-OT, phishing-craft);
RE cross-modal (G10). Current commit-batteries cover AT/revshell/aircrack — the edge of the map, not
the center.

The existing ~400 authored tasks (crypto, detection, network, webcve, deobf, logic, re, protocol,
mitm, cloud, sql, sysadmin, forensics, bugfix, algorithms) are the fastest broadening: harvest them
through the served model and commit-format the PASS set before authoring new domains. New batteries
for the top gaps (pivot/AD, cloud-identity, crypto) are authored deterministically like the AT
battery — real tool output captured as ground truth, wrong-vs-right correction pairs.

## Generation rules for the model

The served model generates under the operating rules in `harness/gh0stx-rules.md` (anti-loop, commit,
verify-before-answer). Key ones for data quality:
produce the real answer by running the tool, not from memory; inspect before acting; run the tool's
own help before writing against it; land the answer, do not spiral. Sampling is fixed at temp 1.0 /
top_p 0.95 / top_k 20 (`SAMPLING.md`) — greedy drives loops and non-commit.

Volume: batch across domains, dedup on (prompt, final answer), keep only self-tested PASS. Terse
targets that match the terse inference ask — a battery diluted in a big off-topic corpus is a
documented past failure (`APPROACH.md`, `[[sft-format-coverage-lesson]]`). Adequate weight per skill
over raw count.

## Scripts

- Generate: `harness/gen_*.py`, `harness/batteries/*_battery_gen.py`, `harness/build_tasks*.py`.
- Harvest: `dflash_deploy.sh` (serve), `harness/run_authored.sh`, `harness/export_sft.py`.
- Commit-format + validate: `reformat_commit_battery.py`, `validate_trainpath.py`.
- Assemble + train: `assemble_focus.py`, `train_141b_requant.py` (`RESUME=1`, `SAVE_STEPS`).
- A/B the result: serve + `eval_run.py` on `audit_msgs.json` / `offensive_msgs.json`, or local
  generation (fix cache_position first — the no-cache path is correct but slow).
