#!/bin/bash
# Nightly reappear self-training timer. Rollback: sudo systemctl disable --now rosorin-reappear.timer &&
# sudo rm /etc/systemd/system/rosorin-reappear.{service,timer} && sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-reappear.service /home/burgerbarn/setup/rosorin-reappear.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rosorin-reappear.timer
systemctl list-timers rosorin-reappear.timer --no-pager | head -3
