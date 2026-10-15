#!/bin/bash
# Install rosorin-explore.service (NOT enabled at boot; one run per `systemctl start`). Rollback: rollback_explore_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-explore.service /etc/systemd/system/rosorin-explore.service
systemctl daemon-reload
systemctl status rosorin-explore.service --no-pager | head -3 || true
