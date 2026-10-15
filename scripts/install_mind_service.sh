#!/bin/bash
# Install + enable rosorin-mind.service (vision/mind.py). Rollback: sudo bash ~/setup/rollback_mind_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-mind.service /etc/systemd/system/rosorin-mind.service
systemctl daemon-reload
systemctl enable --now rosorin-mind.service
sleep 10; systemctl --no-pager --lines=6 status rosorin-mind.service | head -10
