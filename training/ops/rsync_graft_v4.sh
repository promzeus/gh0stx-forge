#!/usr/bin/env bash
GX=192.168.8.239
L=/private/tmp/claude-503/-Users-flash-gb10/9698b8ec-be3d-495b-98d5-10e0b424bba5/scratchpad/graft_v4.safetensors
DST=/tmp/graft/model-graft-00001.safetensors
FULL=11926408392
for i in $(seq 1 15); do
  cur=$(ssh -o ConnectTimeout=8 gh0stx@$GX "stat -c %s $DST 2>/dev/null || echo 0")
  echo "attempt $i: box has $cur/$FULL"
  [ "$cur" -ge "$FULL" ] && { echo "COMPLETE"; break; }
  rsync --partial --append-verify --timeout=90 -e "ssh -o ServerAliveInterval=15 -o ConnectTimeout=8" \
    "$L" "gh0stx@$GX:$DST" 2>&1 | tail -1
  sleep 2
done
echo "final box size: $(ssh gh0stx@$GX "stat -c %s $DST 2>/dev/null")"
