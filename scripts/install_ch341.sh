#!/bin/bash
# Install mainline ch341 (built from NVIDIA R36.4.3 kernel source) + /dev/lidar udev rule.
# Rollback: sudo bash ~/setup/rollback_ch341.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
KREL=$(uname -r); SRC=/home/burgerbarn/build/ch341/ch341.ko
DST=/lib/modules/$KREL/updates/ch341/ch341.ko; RULE=/etc/udev/rules.d/60-rosorin-lidar.rules
[ "$(modinfo -F vermagic "$SRC" | cut -d" " -f1)" = "$KREL" ] || { echo "vermagic mismatch"; exit 1; }
[ -e "$DST" ] && { echo "already installed at $DST"; exit 1; }
install -D -m 0644 "$SRC" "$DST"
depmod -a "$KREL"
modprobe ch341   # 2026-10-12: without it there is no ttyUSB until a reboot, and the ls below stops the script
echo 'SUBSYSTEM=="tty", KERNELS=="1-2.2.4:1.0", SYMLINK+="lidar"' > "$RULE"
udevadm control --reload
udevadm trigger --subsystem-match=tty --action=add
sleep 1
echo "== verify"
modinfo -n ch341; sha256sum "$DST"
ls -l /dev/lidar /dev/ttyUSB* 2>&1
echo "CH341_INSTALLED"
