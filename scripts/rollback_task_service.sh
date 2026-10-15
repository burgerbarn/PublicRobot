#!/bin/bash
# Undo install_task_service.sh: stop any task (their exit disables the wheels), remove the template.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl stop 'rosorin-task@*' || true
rm -f /etc/systemd/system/rosorin-task@.service
systemctl daemon-reload
