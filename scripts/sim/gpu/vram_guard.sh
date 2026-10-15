#!/bin/bash
# Simulated rebuild, on the real bigbuddy: protects the owner's assistant on the shared RTX 5070 Ti.
# Every 250 ms: if the lbsim-gpu-* containers' GPU processes use more than 3584 MiB together, or the GPU has less
# than 768 MiB free, stop those containers (docker stop -t 2) and log it. Run: nohup ~/lbsim/bin/vram_guard.sh &
LOG=~/lbsim/gpu/vram_guard.log; CAP=3584; FLOOR=768
echo "$(date -Is) guard start cap=${CAP}MiB floor=${FLOOR}MiB" >> "$LOG"
while :; do
  names=$(docker ps --format '{{.Names}}' --filter name=lbsim-gpu-)
  if [ -n "$names" ]; then
    ids=$(docker inspect -f '{{.Id}}' $names 2>/dev/null)
    sum=0
    while IFS=', ' read -r pid mem; do
      [ -n "$pid" ] || continue
      grep -qF -f <(echo "$ids") /proc/"$pid"/cgroup 2>/dev/null && sum=$((sum+mem))
    done < <(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits)
    free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
    if [ "$sum" -gt "$CAP" ] || [ "$free" -lt "$FLOOR" ]; then
      echo "$(date -Is) STOP $names: sidecar ${sum}MiB, gpu free ${free}MiB" >> "$LOG"
      docker stop -t 2 $names >> "$LOG" 2>&1
    fi
  fi
  sleep 0.25
done
