#!/bin/bash
# Install rosorin-nav.service: navigation always on from boot (docs/research/always_on_navigation_reference.md).
# Replaces the older nav keeper (rosorin-navkeeper, removed). Run on the robot: sudo bash ~/setup/install_nav_service.sh
# Rollback: sudo systemctl disable --now rosorin-nav; sudo rm /etc/systemd/system/rosorin-nav.service; sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now rosorin-navkeeper.service 2>/dev/null || true
rm -f /etc/systemd/system/rosorin-navkeeper.service
install -m 0644 /home/burgerbarn/setup/rosorin-nav.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable rosorin-nav.service
systemctl restart rosorin-nav.service
sleep 3; systemctl --no-pager --lines=3 status rosorin-nav.service | head -6
