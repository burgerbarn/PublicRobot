#!/usr/bin/env bash
# Simulated rebuild: start the simulated fresh bigbuddy (container lbsim-bb, 172.30.0.30 on network lbsim) for
# chapters 25-28. GPU through NVIDIA's container toolkit (the host driver; installing the driver inside is
# SKIP-NO-SIM). bigbuddy's real USB/Bluetooth/Wi-Fi views hidden; the LAN is cut off like the robot's.
# The sudo password is generated into ~/lbsim/home/.sim_bb_password (mode 600); SSH with the simulation's throwaway key.
set -euo pipefail
cd ~/lbsim/bigbuddy
docker build -q -t lbsim-bb:fresh . >/dev/null
docker rm -f lbsim-bb >/dev/null 2>&1 || true
docker run -d --name lbsim-bb --hostname bigbuddy --gpus all -e NVIDIA_DRIVER_CAPABILITIES=all \
  --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --security-opt label=disable \
  --tmpfs /run --tmpfs /run/lock --tmpfs /tmp --cap-add NET_ADMIN \
  --mount type=tmpfs,destination=/sys/bus/usb --mount type=tmpfs,destination=/sys/class/bluetooth \
  --mount type=tmpfs,destination=/sys/class/ieee80211 \
  --network lbsim --ip 172.30.0.30 lbsim-bb:fresh /sbin/init >/dev/null
for i in $(seq 1 30); do s=$(docker exec lbsim-bb systemctl is-system-running 2>/dev/null || true); case "$s" in running|degraded) break;; esac; sleep 2; done
echo "systemd: $s"
umask 077; P=~/lbsim/home/.sim_bb_password
[ -s $P ] || head -c 18 /dev/urandom | base64 | tr -d '/+=' > $P
docker exec -i lbsim-bb bash -c 'read -r pw; echo "burgerbarn:$pw" | chpasswd' < $P
docker exec -i lbsim-bb bash -c 'install -d -m 700 -o burgerbarn -g burgerbarn /home/burgerbarn/.ssh; cat > /home/burgerbarn/.ssh/authorized_keys; chown burgerbarn:burgerbarn /home/burgerbarn/.ssh/authorized_keys; chmod 600 /home/burgerbarn/.ssh/authorized_keys' < ~/lbsim/home/.sim_mac_key.pub
docker exec lbsim-bb ip route replace prohibit 192.168.0.0/16
docker exec lbsim-bb bash -c 'id burgerbarn; nvidia-smi -L 2>&1 | head -1; ip route | grep -E "prohibit|default"'
