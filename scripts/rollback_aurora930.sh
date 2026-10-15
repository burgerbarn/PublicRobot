#!/bin/bash
# Undo install_aurora930.sh
set -uo pipefail
pkill -INT -f deptrum 2>/dev/null; sleep 1
rm -rf /home/burgerbarn/vendor_ws
sudo rm -f /etc/udev/rules.d/60-rosorin-aurora930.rules
sudo udevadm control --reload
ls /home/burgerbarn/vendor_ws /etc/udev/rules.d/60-rosorin-aurora930.rules 2>&1 | head -2
echo AURORA930_ROLLED_BACK
