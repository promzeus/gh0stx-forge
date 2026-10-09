# SFT commit-format contract

The rule every SFT target must follow so the model commits its answer instead of leaving it inside
the reasoning block. Derived and validated 2026-09-09 against the AT non-commit failure.

## Failure

Ask a question, the model reasons to the correct answer inside `<think>`, then never emits it as
the final answer. On the AT battery: the model reasons `AT+CGSN` in the thinking block, `</think>`
closes (or never closes), and the served response body is empty. The AT command lands in
`reasoning_content`, not `content`.

Not a model-memory gap. It is a data-format gap, the failure mode named in the CoT-SFT literature
(Phi-4-reasoning, open-r1): the reasoning trace outweighs the short answer in per-token loss, so the
transition "long reasoning → committed answer" is undertrained.

## Why inference is always thinking-mode

`chat_template.jinja` appends `<|im_start|>assistant\n<think>\n` on every generation prompt unless
`enable_thinking=false`. Serve runs `--reasoning-parser qwen3` (`dflash_deploy.sh`), which splits the
stream on `</think>`: everything before goes to `reasoning_content`, everything after to `content`.
A target with no `</think>` and no committed answer after it produces empty `content` at the API.

The template stores an assistant turn as `<think>\n{reasoning}\n</think>\n\n{content}` (line 101) and
splits `content` on `</think>` if the tag is present (lines 94-96). A target whose assistant message
carries no `<think>` renders as empty-think + full body, which teaches empty reasoning, not the
long-reason-then-land transition the base reasoning model performs.

## Rule

Every commit-critical target's assistant message:

```
<think>
{decision reasoning: which command/approach and why, confusable wrong options, verify-not-recall}
</think>

{committed answer: the real output, value, or payload, plus its interpretation}
```

Train-format = inference-format. The reasoning names the decision; the answer value lives only in the
commit.

## Anti-rake

The captured answer VALUE must not appear inside `<think>` — not the IMEI digits, not the `+CSQ`
reading, not the decrypted plaintext. Leaking the value into the reasoning re-teaches the exact
non-commit failure. The command name and the decision belong in `<think>`; the module transcript,
the number, the payload output belong after `</think>`. Category words (`OK`, `ERROR`) in the
reasoning are fine — they state the expected shape, not the answer.

Guard: scan `<think>` for `\d{5,}` runs and reading patterns (`+CSQ: n,n`) drawn from the answer's
code blocks. `reformat_commit_at.py` enforces this; `validate_commit_at.py` confirms zero value leaks
and that the qwen3 split returns non-empty `content` carrying the command.

## Per-corpus treatment

Formulaic batteries (AT 83, revshell 163, aircrack 26) — terse answer already committed, wrap with a
short decision `<think>`. Reformatter keyed off the battery's structured fields (command, wrong→right
for corrections). Reasoning stays short: terse tasks land fast, which also trains anti-loop discipline.

Harvested trajectories (sec 662, hard 92) — multi-turn ReAct (`s u a t a t … a`), each assistant turn
reasons then calls a tool, the final turn commits the answer. Rendered through the real train path
(`train_aeon_qlora.py` `normalize()` + `apply_chat_template`, add_generation_prompt=False), every
final assistant turn is non-empty (verified: 662+92, zero empty commits). They already commit — the
non-commit failure is the single-turn terse case, not these. No reformat needed for the commit fix.
Wrapping each intermediate turn's reasoning in `<think>` is an optional later refinement, not required
here. A bare `apply_chat_template` without `normalize()` throws `Can only get item pairs from a
mapping` on these (tool_calls need shaping). Validate through the train path, not a raw render.

## AT battery inclusion

The AT battery (83) is the capability that failed and it was absent from the 917-example AEON set
(917 = sec 662 + hard 92 + revshell 163, zero AT, zero `<think>`). Any commit-fix round must include
the AT battery in commit-format, or it does nothing for the case that failed. Volume does not
substitute — the 30k-example `sft_v5big` already carried the AT battery diluted and still failed.

## Loss

Per-token loss normalization (normalize by response-token count per example) so a long reasoning trace
does not dominate the gradient over the short committed answer. Completion-only masking (prompt tokens
`-100`) is standard. Optionally weight the post-`</think>` commit span. This is the direct lever
against the token imbalance that causes the non-commit.

## Validation gate

Before training on any reformatted corpus: render through `chat_template.jinja`, apply the qwen3
reasoning split, assert `content` (post-`</think>`) is non-empty and carries the expected answer token
(the AT command, the payload, the value). A target that fails this gate reproduces the non-commit.

## Status

Batteries in commit-format, validated against the train path (render_ok, zero empty commits, zero
value leaks): `at_battery_commit_sft.jsonl` (83), `revshell_battery_commit_sft.jsonl` (163),
`aircrack_battery_commit_sft.jsonl` (26). Scripts on box: `reformat_commit_battery.py` (build),
`validate_trainpath.py` (gate). Harvested sec/hard render and commit as-is.

Corpus assembly for the next 141B round: 272 commit-batteries + sec 662 + hard 92, optional WRN
anti-forgetting slice. AT battery MUST be the commit version, not the old `at_battery_sft.jsonl`.
