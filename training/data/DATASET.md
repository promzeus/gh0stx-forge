# Training corpus

`sft_v5big.jsonl` — the live SFT corpus (v5 round). 32061 examples, format
`{"messages":[{role:system},{role:user},{role:assistant}], "meta":{...}}`. Uploaded to
`s3://ai-monitoring-test-123456789012/gh0stx-rft/sft.jsonl` and trained via ZeRO-3
(`../train/job-qlora-zero3.yaml`, EPOCHS=1).

## Composition (32061)

- core 2373 ×2 = 4746 — harvest PASS trajectories + curated batteries, oversampled ×2 so
  the small skills survive dilution.
- WhiteRabbitNeo 27315 — Code-Functions-Level-Cyber (8434) + WRN-Chapter-1 (7747) +
  WRN-Chapter-2 (11134), deduped, refusal-filtered (~0.1%), endpoint-clean (poison scan:
  no systematic exfil host, no hardcoded wallet — only interactsh/placeholders).

core 2373 breaks down as: harvest sec_sft (662) + gradeable-task harvest + curated
batteries AT (83) + aircrack (26) + revshell (163). Battery outputs live on the box under
`~/octest/night/dataset/`; regenerate from `../harness/batteries/*.py`.

## Slices

- `sft_v5.jsonl` — 4373, core + batteries without the WRN bulk (reference / ablation).
- `sft.jsonl`, `sft.clean.jsonl` — 1180/older v3 slice, superseded.

## Provenance chain

`harness/generators/*.py` emit gradeable task banks → `harness/tasks/*.json`. Harvest runs
those through the served model (rejection sampling, PASS → SFT). Curated batteries come from
`harness/batteries/*.py`. WRN is pulled from HF (gated, account promzeus) and filtered.
