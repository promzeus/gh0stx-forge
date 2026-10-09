#!/usr/bin/env bash
GX=192.168.8.239; FULL=11926408392; F=/tmp/graft/model-graft-00001.safetensors
for i in $(seq 1 90); do
  sleep 20
  R=$(ssh -o ConnectTimeout=8 gh0stx@$GX "echo \"\$(stat -c %s $F 2>/dev/null):\$(pgrep -f 'curl.*model-graft'|grep -v pgrep|wc -l)\"" 2>/dev/null)
  sz=${R%%:*}; run=${R##*:}
  [ -z "$sz" ] && { echo "iter $i: vpn flap, skip"; continue; }
  echo "iter $i: $sz/$FULL ($((sz*100/FULL))%) curl=$run"
  [ "$sz" -ge "$FULL" ] && { echo "DOWNLOAD COMPLETE"; exit 0; }
  if [ "$run" = "0" ] && [ "$sz" -lt "$FULL" ]; then echo "curl DIED at $sz, restart needed"; exit 1; fi
done
echo "TIMEOUT"; exit 2
