#!/bin/bash
# Undo install_rtw89.sh
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
KREL=$(uname -r)
for m in $(lsmod | awk '/^rtw89/{print $1}'); do modprobe -r "$m" 2>/dev/null; done
rm -rf /lib/modules/$KREL/updates/rtw89
depmod -a "$KREL"
lsmod | grep rtw89 || echo "no rtw89 loaded"
echo RTW89_ROLLED_BACK
