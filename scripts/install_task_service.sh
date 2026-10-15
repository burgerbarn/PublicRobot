#!/bin/bash
# Install rosorin-task@.service (come | home | sethome; NOT enabled at boot; one run per start). Removes the older
# single-purpose rosorin-come.service. Rollback: rollback_task_service.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl stop rosorin-come.service 2>/dev/null || true
rm -f /etc/systemd/system/rosorin-come.service
install -m 0644 /home/burgerbarn/setup/rosorin-task@.service /etc/systemd/system/rosorin-task@.service
systemctl daemon-reload
systemctl status rosorin-task@come.service --no-pager | head -3 || true
