#!/bin/bash
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now rosorin-api.service 2>/dev/null
rm -f /etc/systemd/system/rosorin-api.service
systemctl daemon-reload
systemctl is-enabled rosorin-api.service 2>&1 | head -1
