#!/usr/bin/env python3
"""
Pre-stage the 397B teacher HF -> S3 (same region eu-north-1) on a cheap CPU pod,
so the expensive H200 run later does a fast S3->PVC sync instead of a ~2h HF download.
Streams file-by-file (download one shard -> upload -> delete) so local disk stays small.
Resumable: skips files already present in S3. Auth via EKS Pod Identity (SA role).
"""
import os, time, boto3
from huggingface_hub import HfApi, hf_hub_download

REPO = os.environ.get("REPO", "Qwen/Qwen3.5-397B-A17B")
BUCKET = os.environ["S3_BUCKET"]
PREFIX = os.environ.get("PREFIX", "qwen3.5-397b-bf16")
SCRATCH = os.environ.get("SCRATCH", "/scratch")
os.makedirs(SCRATCH, exist_ok=True)
t0 = time.time()
def log(*a): print(f"[{(time.time()-t0)/60:6.1f}m]", *a, flush=True)

api = HfApi()
s3 = boto3.client("s3", region_name="eu-north-1")
files = list(api.list_repo_files(REPO))
log(f"repo {REPO}: {len(files)} files -> s3://{BUCKET}/{PREFIX}/")

# resumable: what's already in S3 (key -> size)
existing = {}
pag = s3.get_paginator("list_objects_v2")
for pg in pag.paginate(Bucket=BUCKET, Prefix=PREFIX + "/"):
    for o in pg.get("Contents", []):
        existing[o["Key"]] = o["Size"]
log(f"already in S3: {len(existing)} objects")

done = 0; total_gb = 0.0
for i, f in enumerate(files):
    key = f"{PREFIX}/{f}"
    if existing.get(key, 0) > 0:
        done += 1; continue
    log(f"[{i+1}/{len(files)}] downloading {f}")
    p = hf_hub_download(REPO, f, local_dir=SCRATCH)
    sz = os.path.getsize(p)
    s3.upload_file(p, BUCKET, key)
    try: os.remove(p)
    except OSError: pass
    done += 1; total_gb += sz/1e9
    log(f"  uploaded {f} ({sz/1e9:.2f}GB) | {done}/{len(files)} done | {total_gb:.1f}GB this run")

# verify count + write completion marker (run397b waits for this before consuming)
n_s3 = sum(1 for pg in pag.paginate(Bucket=BUCKET, Prefix=PREFIX + "/") for _ in pg.get("Contents", []))
n_repo = len(files)
if n_s3 >= n_repo:
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIX}/_STAGE_COMPLETE", Body=f"files={n_s3}".encode())
    log(f"STAGE COMPLETE: {n_s3}/{n_repo} objects in s3://{BUCKET}/{PREFIX}/ ; marker written")
else:
    log(f"WARNING: only {n_s3}/{n_repo} objects — marker NOT written (incomplete)")
