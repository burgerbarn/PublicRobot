#!/bin/bash
# Harness shim (GPU sidecar): tegrastats-format lines (the fields vision/bench_live.py parses) from nvidia-smi and
# /proc. The numbers are bigbuddy's (whole-host CPU %, discrete GPU power), not a Jetson's.
iv=1000; [ "$1" = --interval ] && iv=$2
read -r _ u n s i _ < /proc/stat; pb=$((u+n+s)); pt=$((u+n+s+i))
while :; do
  sleep "$(awk -v i="$iv" 'BEGIN{print i/1000}')"
  read -r _ u n s i _ < /proc/stat; b=$((u+n+s)); t=$((u+n+s+i))
  cpu=$(( (b-pb)*100 / ((t-pt) > 0 ? (t-pt) : 1) )); pb=$b; pt=$t
  IFS=', ' read -r gpu pw mem <<<"$(nvidia-smi --query-gpu=utilization.gpu,power.draw,memory.used --format=csv,noheader,nounits)"
  ram=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print int((t-a)/1024)"/"int(t/1024)}' /proc/meminfo)
  echo "$(date '+%m-%d-%Y %H:%M:%S') RAM ${ram}MB CPU [${cpu}%@0] GR3D_FREQ ${gpu}% VDD_IN ${pw%.*}000mW/${pw%.*}000mW VRAM ${mem}MB"
done
