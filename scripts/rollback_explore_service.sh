#!/bin/bash
# Undo install_explore_service.sh: stop (explorer + nav_stop disable wheels), remove the unit.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl stop rosorin-explore.service || true
rm -f /etc/systemd/system/rosorin-explore.service
systemctl daemon-reload
