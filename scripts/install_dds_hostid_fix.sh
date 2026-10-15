#!/bin/bash
# rosorin-base waits for a stable IPv4 address list before any ROS process starts (scripts/wait_addresses.sh).
# Run on the robot: sudo bash ~/setup/install_dds_hostid_fix.sh
# Rollback: sudo rm -r /etc/systemd/system/rosorin-base.service.d/10-wait-addresses.conf; sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
mkdir -p /etc/systemd/system/rosorin-base.service.d
cat > /etc/systemd/system/rosorin-base.service.d/10-wait-addresses.conf <<'UNIT'
[Service]
ExecStartPre=/bin/bash /home/burgerbarn/setup/wait_addresses.sh
TimeoutStartSec=120
UNIT
systemctl daemon-reload
systemctl cat rosorin-base | grep -A3 "10-wait-addresses"
