#!/bin/bash
# Nightly owner-face self-training timer. Rollback: sudo systemctl disable --now rosorin-owner-faces.timer &&
# sudo rm /etc/systemd/system/rosorin-owner-faces.{service,timer} && sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-owner-faces.service /home/burgerbarn/setup/rosorin-owner-faces.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rosorin-owner-faces.timer
systemctl list-timers rosorin-owner-faces.timer --no-pager | head -3
