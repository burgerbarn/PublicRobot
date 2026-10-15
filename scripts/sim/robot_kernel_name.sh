#!/usr/bin/env bash
# Simulated rebuild: make the simulated robot report the Jetson's kernel release (uname -r = 5.15.148-tegra) instead
# of bigbuddy's x86 kernel. qemu-user reads QEMU_UNAME from each program's environment, so it is set for login
# sessions (/etc/environment, read by pam_env) and for systemd services (DefaultEnvironment). Harness only: the real
# robot runs 5.15.148-tegra natively. On bigbuddy: ~/lbsim/bin/robot_kernel_name.sh
# Also NVIDIA's apt source: on a Jetson /etc/systemd/nvfb.sh replaces <SOC> with t234 at first boot from
# /proc/device-tree, which a container does not have (found by the chapter 9 run: apt-get update 404).
set -euo pipefail
docker exec lbsim-robot bash -c '
grep -q "^QEMU_UNAME=" /etc/environment || echo "QEMU_UNAME=5.15.148-tegra" >> /etc/environment
mkdir -p /etc/systemd/system.conf.d
printf "[Manager]\nDefaultEnvironment=QEMU_UNAME=5.15.148-tegra\n" > /etc/systemd/system.conf.d/zz-lbsim-uname.conf
systemctl daemon-reexec
sed -i "s/<SOC>/t234/g" /etc/apt/sources.list.d/nvidia-l4t-apt-source.list 2>/dev/null || true
QEMU_UNAME=5.15.148-tegra uname -r'
