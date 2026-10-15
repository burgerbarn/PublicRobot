#!/bin/bash
# Install + enable rosorin-base.service. Rollback: sudo bash ~/setup/rollback_base_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/ros2_ws/src/rosorin_base/systemd/rosorin-base.service /etc/systemd/system/rosorin-base.service
systemctl daemon-reload
systemctl enable --now rosorin-base.service
sleep 8; systemctl --no-pager --lines=8 status rosorin-base.service | head -12
