#!/bin/bash
# Install + enable rosorin-api.service (robot API for Buddy, :8296, token in ~/.config/rosorin/api_token).
# Rollback: sudo bash ~/setup/rollback_api_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-api.service /etc/systemd/system/rosorin-api.service
systemctl daemon-reload
systemctl enable --now rosorin-api.service
sleep 8; systemctl --no-pager --lines=4 status rosorin-api.service | head -6
