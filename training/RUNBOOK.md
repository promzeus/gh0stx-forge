# gh0stx RFT pipeline — runbook

End-to-end for one RFT round: corpus → ZeRO-3 LoRA on AWS → merge → NVFP4 graft-requant →
assemble release on the box → DFlash deploy → verify. Every manifest and script referenced
here lives in this tree; secrets do not (see Credentials).

## Topology

Three machines, each owning distinct stages.

- AWS EKS cluster `example-cluster` (eu-north-1), namespace `distill` — training (8×H200/H100
  spot) and the CPU graft-requant. kube context must be `example-cluster`.
- Box `gx10` (`gh0stx@192.168.8.239`) — serves the model (vLLM + DFlash), runs opencode
  harvest, assembles the release, deploys. Reached over LAN/VPN from the mac.
- mac — orchestration only: `kubectl`, `aws`, `ssh`, dataset staging. Holds no long-running
  compute.

## Credentials and keys

No secret material is committed. The pipeline authenticates as follows.

- AWS: the training/quant pods carry no IRSA. ServiceAccount `ai-monitoring-test`
  (`infra/sa-ai-monitoring-test.yaml`) has no `role-arn` annotation; pods inherit the
  node instance-profile role `Karpenter-example-cluster-20250502...` set in
  `infra/ec2nodeclass-gpu-distill.yaml`. That role grants S3 to bucket
  `ai-monitoring-test-123456789012`. Nothing to provision per-round.
- mac AWS: AWS SSO (IAM Identity Center), no static keys (`~/.aws/credentials` empty). kube
  context `example-cluster` resolves through `source_profile = example-stage`; profile `example-stage`
  assumes AWSAdministratorAccess in account 123456789012 — the same account as the training
  cluster and the S3 bucket. Refresh the session with `aws sso login --profile example-stage`,
  `export AWS_PROFILE=example-stage`. The box has no AWS creds and reaches S3 only through
  presigned URLs the mac signs.
- HuggingFace: token lives on the box (account `promzeus`), used for gated dataset pulls
  (WhiteRabbitNeo). Rotate with `huggingface-cli login` on the box, never through chat. The
  AWS jobs never touch HF — they pull the base from S3.
- SSH: mac→box over `~/.ssh/id_rsa`. Not in the repo.

## S3 layout — bucket ai-monitoring-test-123456789012

- `decensored_v4_fused/` — fused-expert bf16 base (282GB, 61 shards). from_pretrained loads
  it clean under zero.Init; this is what training downloads.
- `decensored_v4/` — per-expert base (pre-fusion). `repack_experts.py` fuses it; kept for
  rebuilds.
- `gh0stx-rft/sft.jsonl` — the training corpus (upload target for `data/sft_v5big.jsonl`).
- `gh0stx-rft/adapter/` — LoRA adapter out.
- `gh0stx-rft/merged/` — merged bf16 (31 shards, 282GB), input to requant.
- `dflash_meta/` — NVFP4 scales (`weight_global_scale` + `input_global_scale`) the requant
  reuses. Must carry both; a wgs-missing meta fails the quant preflight.

## Stage 0 — infra bring-up (once per cluster)

```
kubectl --context example-cluster apply -f infra/namespace-distill.yaml
kubectl --context example-cluster apply -f infra/ec2nodeclass-gpu-distill.yaml
kubectl --context example-cluster apply -f infra/nodepool-gpu-distill-h200.yaml
kubectl --context example-cluster apply -f infra/ec2nodeclass-cpu-prune.yaml
kubectl --context example-cluster apply -f infra/nodepool-cpu-prune.yaml
kubectl --context example-cluster apply -f infra/sa-ai-monitoring-test.yaml
```

NodePool `gpu-distill-h200`: p5e/p5en/p5.48xlarge, spot-preferred, taint `test-gpu:NoSchedule`
(the job tolerates it), limits 192 CPU / 8 GPU. NodePool `cpu-prune`: big-RAM r7i for the
262GB-in-RAM requant. Nodes are Karpenter-provisioned on demand; a warm node skips the
~5min provision.

## Stage 1 — corpus → S3

`data/sft_v5big.jsonl` (32061, provenance in `data/DATASET.md`) is the round corpus.

```
aws s3 cp data/sft_v5big.jsonl \
  s3://ai-monitoring-test-123456789012/gh0stx-rft/sft.jsonl
# low-concurrency if the endpoint flaps:
aws configure set default.s3.max_concurrent_requests 2
```

## Stage 2 — ZeRO-3 LoRA train + merge

```
bash ops/build-configmaps.sh                       # ships /code from train/ + quant/
kubectl --context example-cluster apply -f train/job-qlora-zero3.yaml
```

`entrypoint.sh` (ZERO3 branch): downloads the fused base, `torchrun --nproc_per_node=8`,
`train_qlora.py` `main_zero3()` shards under DeepSpeed zero.Init, LoRA on attention +
linear_attn + shared_expert (routed experts frozen), then `merge_adapter.py` writes merged
bf16 to `MERGED_S3`. Params in the job env: `EPOCHS`, `MAXLEN=8192`, `GRAD_ACCUM`,
`SAVE_STEPS`. Effective batch = 8 GPU × per_device 1 × accum.

Monitor:
```
kubectl --context example-cluster -n distill logs -f -l app=gh0stx-qlora-zero3 | grep -E "loss|epoch"
```
Healthy = loss descending, grad_norm ~0.1, no OOM. A completion watcher lives in
`ops/watch_train_done.sh`.

## Stage 3 — NVFP4 graft-requant

Only attention + shared_expert change under LoRA. `graft_requant.py` MODE=quant re-quantizes
those ~240 modules to NVFP4 reusing the dflash scales, copies 225 linear_attn as bf16 →
graft shard (~12GB, 1185 keys). Preflight in `quant/entrypoint_quant.sh` checks imports +
both scale keys before the 262GB sync.

```
kubectl --context example-cluster apply -f quant/job-quant.yaml     # cpu-prune nodepool
```

`dflash_meta` must hold `weight_global_scale`; recomputing it desyncs the pairing and
produces garbage (guard in `graft_requant.py`). If the meta is wgs-missing, rebuild it on
the box from `nvfp4_v4_releases/dflash_only` and re-upload.

## Stage 4 — deliver graft to box, assemble, deploy

The box cannot reach S3 directly. Sign a URL on the mac, pull on the box by its own WAN
(survives VPN flaps far better than ssh-tunneled rsync — see `ops/stream_graft_v4.sh`,
`ops/rsync_final.sh` for the fallbacks tried).

```
# assemble: hardlink dflash_only trunk + drop in the graft shard
MODE=assemble ... python3 quant/graft_requant.py        # on box, -> nvfp4_v4_releases/gh0stx_rft_vN
```

Deploy through the box Makefile (`deploy/Makefile.box`), parameterized release + drafter:
```
GH0STX_RELEASE=/home/gh0stx/nvfp4_v4_releases/gh0stx_rft_vN make dflash
```
`deploy/dflash_deploy.sh` starts `aeon-vllm:v024`, 90k ctx, mamba float16, served-model-name
`gh0stx`, port 8000; waits health.

## Stage 5 — verify

AT knowledge + agentic checks against the served model (the persistent gap: SIM800C AT
vocabulary). Confirm no invented commands (v4 curated battery fixed AT+CGSN/CIMI/CSQ/CREG?).

## Anti-forgetting

Each round trains fresh from the frozen fused base on the cumulative corpus; the risk is
dilution, not catastrophic loss. Small skills (AT/aircrack/revshell batteries) are
oversampled so they survive the WRN bulk. Never resume-train on top of a prior adapter.
