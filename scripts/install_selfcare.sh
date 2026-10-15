#!/bin/bash
# The robot's self-care loop: rosorin-selfcheck.service + timer (docs/research/doing_it_itself_reference.md).
# Run on the robot: sudo bash ~/setup/install_selfcare.sh
# Off switch for the owner (nothing is changed any more, it still measures): touch ~/selfcare/DISABLED
# Rollback: sudo systemctl disable --now rosorin-selfcheck.timer; sudo rm /etc/systemd/system/rosorin-selfcheck.{service,timer};
#           sudo rm -f /etc/systemd/system/rosorin-*.service.d/90-selfcare-*.conf; sudo systemctl daemon-reload
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -m 0644 /home/burgerbarn/setup/rosorin-selfcheck.service /home/burgerbarn/setup/rosorin-selfcheck.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now rosorin-selfcheck.timer
systemctl list-timers --no-pager | grep selfcheck
