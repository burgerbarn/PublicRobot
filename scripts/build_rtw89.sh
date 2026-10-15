#!/bin/bash
# Build rtw89 (RTL8852CE Wi-Fi, PCI 10ec:c852) for the running L4T kernel.
# Source: github.com/a5a5aa555oo/rtw89 branch 6.6-lts = backport of mainline Linux rtw89,
# README: kernels 5.15-6.5 only. Stock R36.4.3 kernel (5.15.148-tegra) has no rtw89.
# Runs as a normal user on the robot. Rebuild after any L4T kernel update.
# Usage: bash build_rtw89.sh [commit]   (no arg = branch head; commit is printed, record it)
set -euo pipefail
B=~/build/rtw89
[ -d "$B/.git" ] || git clone -b 6.6-lts https://github.com/a5a5aa555oo/rtw89.git "$B"
cd "$B"
[ -n "${1:-}" ] && git checkout -q "$1"
git log -1 --format='commit %H %cI %s'
make KVER="$(uname -r)" clean >/dev/null
make KVER="$(uname -r)" -j"$(nproc)" modules
for m in *.ko; do printf '%s  %s\n' "$(modinfo -F vermagic "$m" | cut -d' ' -f1)" "$(sha256sum "$m")"; done
echo RTW89_BUILT
