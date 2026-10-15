#!/bin/bash
# Install + enable rosorin-camera.service (Aurora 930 driver at boot, separate from rosorin-base so the vendor
# driver can't take the base down). Rollback: sudo bash ~/setup/rollback_camera_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
# no driver, no service: without this the unit crash-loops (simulated blank-drive rebuild 2026-10-08)
[ -f /home/burgerbarn/vendor_ws/install/setup.bash ] || { echo "no camera driver in ~/vendor_ws (chapter 15): not installing the service"; exit 1; }
pkill -INT -f "aurora930_launch.py" 2>/dev/null && sleep 3 || true      # a hand-started driver would hold the camera
install -m 0644 /home/burgerbarn/setup/rosorin-camera.service /etc/systemd/system/rosorin-camera.service
systemctl daemon-reload
systemctl enable --now rosorin-camera.service
sleep 12; systemctl --no-pager --lines=5 status rosorin-camera.service | head -8 || true   # status exits 3 when not active
