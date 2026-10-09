# Sampling — reasoning models (gh0stx, AEON/Qwen3.x)

Run these reasoning models at the sampling their base was tuned for. Greedy / near-greedy
(temp 0–0.2) drives degenerate loops, non-termination (all tokens spent in the reasoning
channel, empty `content`), and hallucination. This was misread as a model defect.

## Correct sampling
Reasoning tasks: **temperature 1.0, top_p 0.95, top_k 20**, reasoning effort high/xhigh
(Qwen3.8 / z-lab / AEON model cards). Agentic/tool: `enable_thinking:false`, lower temperature.

## Evidence (auth audit of cryptosystems, gh0stx v5)
Same 3-file Go auth module, same audit prompt, three runs:
- temp 0.2, plain prompt → empty content, `finish=length`, 0 findings (all in reasoning).
- temp 0.2, force-commit prompt → committed 8 findings, but a false HIGH ("password stored
  without bcrypt salt" — code uses `bcrypt.GenerateFromPassword`, auto-salted).
- **temp 1.0, plain prompt → clean committed list (5 findings), self-corrects false positives
  (notes allowedFields validation, PasswordHash cleared), no bcrypt hallucination.**

Correct sampling fixed both commit and honesty with no retraining and no force-commit prompt.

## AT re-test at temp 1.0 (partial — not a total fix)
Same AT knowledge probe, temp 1.0: IMSI `AT+CIMI`, signal `AT+CSQ`, reg `AT+CREG?` correct
and committed (3/5). CEER now commits (was empty at greedy) but answers `AT+CERR` — wrong,
standard is `AT+CEER` (a hallucinated typo). IMEI still `finish=length`, empty even at 2048
tokens — genuine non-termination on that prompt, survives correct sampling.

## Consequence
Sampling fixes MOST of the commit/hallucination symptom (audit clean, 3/5 AT correct) but not
all: IMEI non-terminates and CEER hallucinates a wrong command even at temp 1.0. So the
commit-DPO/SFT round (which caused the $800 AWS burn) was mostly unnecessary, but a small real
residual defect remains on specific prompts. Order of operations for any "model errs" report:
set correct sampling FIRST, then a targeted tune only for the residual. Baked into the opencode
`gx10/gh0stx` provider defaults and the AEON serve recipe (`deploy/aeon-27b-dflash.md`).
