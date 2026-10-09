#!/usr/bin/env bash
GX=192.168.8.239; BUCKET=ai-monitoring-test-123456789012
KEY=gh0stx-rft/graft/model-graft-00001.safetensors; FULL=11926408392
F=/tmp/graft/model-graft-00001.safetensors
for i in $(seq 1 20); do
  cur=$(ssh gh0stx@$GX "stat -c %s $F 2>/dev/null || echo 0")
  [ "$cur" -ge "$FULL" ] && { echo "COMPLETE $cur"; break; }
  echo "iter $i: $cur/$FULL"
  FIFO=$(mktemp -u /tmp/gf.XXXX); mkfifo "$FIFO"
  aws s3api get-object --bucket "$BUCKET" --key "$KEY" --range "bytes=$cur-" "$FIFO" >/dev/null 2>/tmp/gerr.log &
  ssh -o ServerAliveInterval=15 gh0stx@$GX "cat >> $F" < "$FIFO"
  rm -f "$FIFO"
done
echo "final: $(ssh gh0stx@$GX "stat -c %s $F")"
