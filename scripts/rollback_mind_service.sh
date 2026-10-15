#!/bin/bash
# Undo install_mind_service.sh: stop, disable, remove the unit (the mind releases the arm on SIGINT).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
systemctl disable --now rosorin-mind.service || true
rm -f /etc/systemd/system/rosorin-mind.service
systemctl daemon-reload
