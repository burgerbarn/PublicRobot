#!/usr/bin/env bash
# Simulated rebuild: (re)start the robot container from an image with bigbuddy's hardware hidden.
# The container shares bigbuddy's kernel, so /sys and /proc would show bigbuddy's real USB devices, its Realtek
# Bluetooth radio and its loaded Wi-Fi modules (found by the chapter 7 run: robot_bt_rtk.sh would have picked
# bigbuddy's radio). Those views are replaced with empty ones; /sys stays read-only; no extra capabilities.
# Usage on bigbuddy: ~/lbsim/bin/robot_run.sh <image>      e.g. lbsim-robot:after-ch19
set -euo pipefail
IMG=$1
# never remove the running robot unless the image to start from exists (2026-10-08: a failed snapshot + rm lost
# the chapter 6-7 state)
docker image inspect "$IMG" >/dev/null 2>&1 || { echo "no image $IMG: robot left as it is"; exit 1; }
docker rm -f lbsim-robot >/dev/null 2>&1 || true
docker run -d --name lbsim-robot --hostname rosorin --platform linux/arm64 \
  --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --security-opt label=disable \
  --tmpfs /run --tmpfs /run/lock --cap-add NET_ADMIN \
  --mount type=tmpfs,destination=/sys/bus/usb --mount type=tmpfs,destination=/sys/class/bluetooth \
  --mount type=tmpfs,destination=/sys/class/ieee80211 --mount type=tmpfs,destination=/sys/module \
  --network lbsim --ip 172.30.0.10 -v ~/lbsim/hw:/opt/lbsim:ro,z "$IMG" /sbin/init >/dev/null
for i in $(seq 1 60); do
  s=$(docker exec lbsim-robot systemctl is-system-running 2>/dev/null || true)
  case "$s" in running|degraded) break;; esac; sleep 5
done
echo "systemd: $s after $((i * 5)) s"
~/lbsim/bin/hw_up.sh
