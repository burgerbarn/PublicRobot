#!/bin/bash
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now rosorin-base.service 2>/dev/null
rm -f /etc/systemd/system/rosorin-base.service
systemctl daemon-reload
systemctl is-enabled rosorin-base.service 2>&1 | head -1
