#!/bin/bash
# deploy_mtp_overlay.sh — atomic MTP-overlay deploy + rollback for gh0stx on gx10.
#
# Restructures the live model path from a flat, always-mutated directory into
# versioned "releases" (base NVFP4 shards hardlinked — no 84GB duplication —
# plus each release's own model-mtp-grafted.safetensors/index.json/config.json)
# activated via a single symlink flip. Rollback = flip the symlink back, never
# a piecemeal re-copy.
#
# Runs the SAME stop->verify-free->cooldown->start->verify-free->cooldown->
# smoke-test discipline for every container restart this script performs,
# with an automated journalctl OOM-signature watcher — per the incident from
# the --moe-backend marlin hang (NVRM ... _memdescAllocInternal looping,
# unrecoverable without a physical power-cycle). See
# /Users/flash/.claude/plans/zazzy-strolling-cascade.md for the full writeup.
#
# Usage (run ON gx10):
#   deploy_mtp_overlay.sh stage   <release_name> <overlay_dir>   # build a release from an overlay (safe, no live touch)
#   deploy_mtp_overlay.sh activate <release_name>                # flip live symlink to a staged release (STOPS/RESTARTS the container)
#   deploy_mtp_overlay.sh rollback <release_name>                # identical to activate, but framed as an incident rollback
#   deploy_mtp_overlay.sh status                                 # what's live right now
set -euo pipefail

BASE_DIR="/home/gh0stx/nvfp4_v4"                       # current always-there base (NOT touched by this script)
RELEASES_DIR="/home/gh0stx/nvfp4_v4_releases"
SERVE_LINK="/home/gh0stx/nvfp4_v4_serve"               # what v4_deploy.sh should mount as /model
CONTAINER="vllm-mtp-v4"
IMAGE="ghcr.io/aeon-7/aeon-vllm-ultimate:latest"
OOM_LOG="/home/gh0stx/oom_watch_$(date +%s).log"
KNOWN_GOOD_FLAGS="vllm serve /model --served-model-name gh0stx --quantization compressed-tensors \
   --gpu-memory-utilization 0.87 --max-model-len 131072 --max-num-seqs 2 \
   --enable-chunked-prefill --enable-prefix-caching --trust-remote-code \
   --language-model-only --load-format runai_streamer --max-num-batched-tokens 8192 \
   --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3 \
   --speculative-config '{\"method\":\"qwen3_5_mtp\",\"num_speculative_tokens\":3}' \
   --host 0.0.0.0 --port 8000"

log() { echo "[deploy-mtp $(date +%H:%M:%S)] $*"; }
err() { echo "[deploy-mtp $(date +%H:%M:%S)] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Safety primitives — reused for every stop/start this script does.
# ---------------------------------------------------------------------------
mem_snapshot() { free -h | grep -E 'Mem|Swap'; }

start_oom_watcher() {
  log "arming OOM-signature watcher -> $OOM_LOG"
  setsid bash -c "journalctl -k -f | grep --line-buffered -iE 'NVRM.*Out of memory|NV_ERR_NO_MEMORY|memdescAllocInternal' >> '$OOM_LOG' 2>&1 &
    WATCHPID=\$!
    while kill -0 \$WATCHPID 2>/dev/null; do
      if [ -s '$OOM_LOG' ]; then
        echo 'OOM SIGNATURE DETECTED — killing watched process' >> '$OOM_LOG'
        pkill -9 -f 'vllm serve' 2>/dev/null || true
        pkill -9 -f vllm_hs_extractor_mtp_calib.py 2>/dev/null || true
        break
      fi
      sleep 1
    done" </dev/null >/dev/null 2>&1 &
  echo $! > /tmp/oom_watcher.pid
}

stop_oom_watcher() {
  [ -f /tmp/oom_watcher.pid ] && kill "$(cat /tmp/oom_watcher.pid)" 2>/dev/null || true
  rm -f /tmp/oom_watcher.pid
}

wait_for_gpu_free() {
  # Bounded poll (<=2 min) — do NOT force through on timeout, escalate instead.
  # NOTE: exclude the OOM watcher's own shell (its embedded `pkill -f 'vllm serve'`
  # text makes it self-match this exact pgrep pattern) and the pgrep call itself.
  local baseline_avail="$1"
  local waited=0
  while [ $waited -lt 120 ]; do
    if ! pgrep -af 'EngineCore|vllm serve' 2>/dev/null | grep -v 'journalctl\|oom_watch\|pgrep -af' | grep -q .; then
      local now_avail
      now_avail=$(free -m | awk '/Mem:/ {print $7}')
      # within ~10GB of baseline is "close enough" — this box's telemetry is imprecise
      if [ "$now_avail" -ge $((baseline_avail - 10240)) ]; then
        log "memory verified free (available=${now_avail}MB vs baseline=${baseline_avail}MB)"
        return 0
      fi
    fi
    sleep 5; waited=$((waited + 5))
  done
  err "memory/process did NOT clear within 2 minutes — STOP, escalate, do not force through. $(mem_snapshot)"
}

stop_live_container() {
  log "baseline: $(mem_snapshot)"
  local baseline_avail
  baseline_avail=$(free -m | awk '/Mem:/ {print $7}')
  log "stopping $CONTAINER (30s grace, not the default 10s — let CUDA teardown finish)"
  sudo docker stop --time 30 "$CONTAINER" 2>/dev/null || true
  sudo docker rm "$CONTAINER" 2>/dev/null || true
  wait_for_gpu_free "$baseline_avail"
  log "cooldown 20s (incident spiral took 14s to show — 'just cleared' != 'driver settled')"
  sleep 20
}

start_live_container() {
  start_oom_watcher
  log "starting $CONTAINER on KNOWN-GOOD flags (no incidental changes)"
  sudo docker run -d --name "$CONTAINER" --gpus all --ipc=host -p 8000:8000 \
    -v "$SERVE_LINK":/model:ro "$IMAGE" -lc "$KNOWN_GOOD_FLAGS"
  log "waiting for readiness (bounded, 5 min)"
  local waited=0
  while [ $waited -lt 300 ]; do
    if curl -s --max-time 4 http://localhost:8000/v1/models 2>/dev/null | grep -q gh0stx; then
      log "model responding"
      break
    fi
    sleep 5; waited=$((waited + 5))
  done
  [ $waited -ge 300 ] && err "server did not become ready within 5 minutes — check docker logs $CONTAINER"
  stop_oom_watcher
  if [ -s "$OOM_LOG" ]; then
    err "OOM signature was detected during this start — see $OOM_LOG. Container was killed. Do not proceed."
  fi
  smoke_test
}

smoke_test() {
  log "smoke-test: one real chat completion"
  local resp
  resp=$(curl -s --max-time 60 http://localhost:8000/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d '{"model":"gh0stx","messages":[{"role":"user","content":"Say hello in one sentence."}],"max_tokens":30,"temperature":0}')
  echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); print('SMOKE-TEST OK:', d['choices'][0]['message'].get('content') or d['choices'][0]['message'].get('reasoning','')[:80])" \
    || err "smoke-test FAILED — response: $resp"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
cmd_stage() {
  local name="$1" overlay_dir="$2"
  [ -d "$overlay_dir" ] || err "overlay dir not found: $overlay_dir"
  local rel="$RELEASES_DIR/$name"
  [ -e "$rel" ] && err "release '$name' already exists at $rel — pick a new name"
  mkdir -p "$rel"
  log "hardlinking base shards from $BASE_DIR (no 84GB copy)..."
  find "$BASE_DIR" -maxdepth 1 -type f \
    ! -name 'model-mtp-grafted.safetensors' \
    ! -name 'model.safetensors.index.json' \
    ! -name 'config.json' \
    -exec ln "{}" "$rel/" \;
  log "copying overlay-specific files from $overlay_dir..."
  cp "$overlay_dir/model-mtp-grafted.safetensors" "$rel/"
  cp "$overlay_dir/model.safetensors.index.json" "$rel/"
  cp "$overlay_dir/config.json" "$rel/"
  sha256sum "$rel"/*.safetensors "$rel"/*.json > "$rel/CHECKSUMS.sha256"
  log "staged release '$name' at $rel ($(du -sh "$rel" | cut -f1) on-disk, mostly hardlinked)"
}

cmd_activate() {
  local name="$1"
  local rel="$RELEASES_DIR/$name"
  [ -d "$rel" ] || err "release '$name' not found at $rel — run 'stage' first"
  log "verifying checksums before activation..."
  (cd "$rel" && sha256sum -c CHECKSUMS.sha256) || err "checksum mismatch in $rel — refusing to activate a corrupted release"
  stop_live_container
  log "flipping $SERVE_LINK -> $rel"
  ln -sfn "$rel" "$SERVE_LINK.new"
  mv -T "$SERVE_LINK.new" "$SERVE_LINK"   # atomic rename, not piecemeal
  start_live_container
  log "ACTIVATED '$name'. Run the MTP-TUNING.md benchmark now and record the result (win or loss)."
}

cmd_rollback() {
  local name="$1"
  log "ROLLBACK requested -> '$name'"
  cmd_activate "$name"
  log "ROLLBACK complete. Smoke-test passed. Verify with a few more real requests before declaring done."
}

cmd_status() {
  echo "SERVE_LINK -> $(readlink -f "$SERVE_LINK" 2>/dev/null || echo '(not yet migrated — still on flat BASE_DIR)')"
  echo "releases:"
  ls -la "$RELEASES_DIR" 2>/dev/null || echo "  (none staged yet)"
  echo "container: $(sudo docker ps --filter name="$CONTAINER" --format '{{.Status}}' 2>/dev/null || echo 'not running')"
}

case "${1:-}" in
  stage)    cmd_stage "$2" "$3" ;;
  activate) cmd_activate "$2" ;;
  rollback) cmd_rollback "$2" ;;
  status)   cmd_status ;;
  *) echo "Usage: $0 {stage <name> <overlay_dir>|activate <name>|rollback <name>|status}"; exit 1 ;;
esac
