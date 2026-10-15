#!/bin/bash
# Undo install_ch341.sh
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
KREL=$(uname -r)
rmmod ch341 2>/dev/null || true
rm -f /lib/modules/$KREL/updates/ch341/ch341.ko; rmdir /lib/modules/$KREL/updates/ch341 2>/dev/null || true
depmod -a "$KREL"
rm -f /etc/udev/rules.d/60-rosorin-lidar.rules
udevadm control --reload
modinfo -n ch341 2>&1 | head -1; ls /dev/ttyUSB* /dev/lidar 2>&1
echo "CH341_ROLLED_BACK"
