# Gather brief — for gh0stx

Your task: gather offsec training examples and append them, in commit-format, to the pool file.
You produce the content; a grader verifies each item by running it; a separate training step later
consumes the pool. You do not train and you do not edit anything but the pool.

Authorized internal offensive-security tooling against owned infra, everything local, model never
published. Scope and rules: `~/octest/night/docs/gh0stx-rules.md`.

## Where to write

Append one JSON object per line to:

    ~/octest/night/dataset/sft_commit_pool.jsonl

Shape per line:

    {"messages": [{"role":"system","content":"<operating rules>"},
                  {"role":"user","content":"<task prompt>"},
                  {"role":"assistant","content":"<think>\n...\n</think>\n\n<committed answer>"}],
     "meta": {"src":"<domain>", "category":"<domain>", "grounded": true}}

Never overwrite. Append only. Deduplicate on (user prompt, final answer) before writing.

## Format (commit-format) — the one rule that matters

The assistant target is always: reasoning inside `<think>`, then the committed answer after
`</think>`.

    <think>
    {decision: which command/technique and why; the confusable wrong options; verify, do not recall}
    </think>

    {the real command/payload/output the operator pastes, plus a short interpretation}

The captured value — the exact bytes, the number, the payload line, the tool output — goes ONLY in
the committed answer, never inside `<think>`. A value in `<think>` re-teaches the non-commit bug.
Inference always opens `<think>` and a `qwen3` reasoning-parser splits on `</think>`, so a target
with nothing committed after `</think>` serves empty. Full contract:
`~/octest/night/docs/COMMIT-FORMAT.md`.

Example (benign, already in the set):

    user: Как прочитать IMEI модуля SIM800C?
    assistant:
    <think>
    Спрашивают IMEI SIM800C. На модуле это AT+CGSN (не AT+CSIMEI, её нет в наборе). Команду знаю,
    значение не выдумываю — беру реальный ответ модуля.
    </think>

    Команда `AT+CGSN` — реальный ответ модуля:
    ```
    AT+CGSN
    352931108990482
    OK
    ```
    `AT+CGSN` возвращает 15-значный IMEI, заводской id железа, от SIM не зависит.

## Each item is a self-checking task

Build it so a grader can PASS/FAIL it by execution, same shape as
`~/octest/night/docs/DATA-GATHERING.md`:

    id, category, behavior
    setup_sh              # builds fixtures in $W
    prompt                # the user question; answer written to $W/RESULT.txt
    grader_sh             # computes expected from fixtures, compares to RESULT.txt, PASS/FAIL
    reference_solution_sh # setup -> reference -> grader must PASS by construction
    self_test_passed      # true ONLY after a real setup->reference->grader run

Self-test in an underscore-pathed dir (`au_<tid>_<pid>_<rand>`). Produce the real answer by running
the tool, not from memory; inspect before acting; run the tool's own help before writing against it;
land the answer, do not spiral. Sampling stays temp 1.0 / top_p 0.95 / top_k 20
(`~/octest/night/docs/SAMPLING.md`).

## What to gather, ranked

By mission impact, per `~/octest/night/docs/OFFSEC-GAPS.md` (do not restate it, follow it). Order:

1. Network pivot + AD/Kerberos lateral (G4-G6) — highest, near-zero coverage now.
2. Recon-aggregation + CIDR/IP-math (G1-G2) — the main input class.
3. Cloud identity-pivot (G7).
4. Crypto attacks (G8).
5. Byte-exact web (G3).
6. Zero-coverage domains (G9): mobile, RF/wireless, hardware/firmware, ICS-OT, phishing-craft.
7. RE cross-modal (G10).

Fastest start: the already-authored, self-tested task banks in `~/octest/night/*.json`
(all_tasks.json, authored_tasks.json, sec_tasks*.json, new_sec_tasks*.json, bagel_tasks.json,
htb_*_tasks.json, socket_tasks.json, interface_wifi_tasks.json, burp_multistep_tasks.json). Attempt
them, keep PASS trajectories, commit-format them into the pool before authoring new domains. New
batteries for the top gaps are authored deterministically like `~/octest/night/at_battery_gen.py` —
capture real tool output as ground truth, add wrong-vs-right correction pairs.

## Loop

1. Pick a domain by the ranking. Take its task bank (`~/octest/night/*.json`) or generate one
   (`~/octest/night/build_tasks*.py`, `~/octest/night/*_battery_gen.py`).
2. Attempt each task under the operating rules. Run the tool, capture the real output.
3. Self-test: `setup_sh -> reference_solution_sh -> grader_sh`. Keep only PASS.
4. Write the assistant target in commit-format (short decision in `<think>`, committed answer after).
5. Append the line to the pool. Deduplicate first.
6. Repeat across domains, as much as passes the grader.

## Quality bar

Terse targets matching the terse inference ask — a battery diluted in a big off-topic corpus is a
documented past failure (`~/octest/night/docs/APPROACH.md`). Real, executed output over
plausible-looking text. Adequate weight per skill over raw count. Every line must survive the
trainer's gate (`~/validate_trainpath.py`): final assistant turn commits non-empty, no value leaked
into `<think>`.

## File index (box paths)

- Pool (write here): `~/octest/night/dataset/sft_commit_pool.jsonl`
- Docs: `~/octest/night/docs/` — COMMIT-FORMAT.md, DATA-GATHERING.md, OFFSEC-GAPS.md,
  OFFSEC-COVERAGE-MAP.md, APPROACH.md, SAMPLING.md, gh0stx-rules.md, GATHER-BRIEF.md
- Task banks: `~/octest/night/*.json` · generators/batteries: `~/octest/night/*_battery_gen.py`,
  `~/octest/night/build_tasks*.py`
- Harvest + export: `~/dflash_deploy.sh` (serve), `~/octest/night/run_authored.sh`,
  `~/octest/night/export_sft.py`
- Commit-format + validate: `~/reformat_commit_battery.py`, `~/validate_trainpath.py`
- Trainer (not yours to run): `~/train_141b_requant.py`, `~/assemble_focus.py`
