#!/bin/bash
# Install rtw89 modules built by build_rtw89.sh. Firmware rtw89/rtw8852c_fw.bin ships in stock rootfs.
# Rollback: sudo bash ~/setup/rollback_rtw89.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
KREL=$(uname -r); SRC=/home/burgerbarn/build/rtw89; DST=/lib/modules/$KREL/updates/rtw89
[ -e "$DST" ] && { echo "already installed at $DST"; exit 1; }
ls "$SRC"/*.ko >/dev/null
for m in "$SRC"/*.ko; do
  [ "$(modinfo -F vermagic "$m" | cut -d' ' -f1)" = "$KREL" ] || { echo "vermagic mismatch $m"; exit 1; }
done
install -d "$DST"; install -m 0644 "$SRC"/*.ko "$DST"/
depmod -a "$KREL"
modprobe rtw89_8852ce_git
sleep 3
echo "== verify"
lsmod | grep rtw89; dmesg | grep -i rtw89 | tail -15
ip -br link | grep -v -E '^(lo|can0|l4tbr0|usb)'
nmcli -f DEVICE,TYPE,STATE dev
echo RTW89_INSTALLED
