#!/bin/bash
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now rosorin-camera.service 2>/dev/null
rm -f /etc/systemd/system/rosorin-camera.service
systemctl daemon-reload
systemctl is-enabled rosorin-camera.service 2>&1 | head -1 || true   # "Failed to get unit file state" = removed
