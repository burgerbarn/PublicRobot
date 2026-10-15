#!/bin/bash
# Keep the systemd journal across reboots (2026-10-02: the stop-failure incident log was lost in the reboot).
# Rollback: sudo rm -rf /var/log/journal && sudo systemctl restart systemd-journald
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
mkdir -p /var/log/journal && systemd-tmpfiles --create --prefix /var/log/journal
journalctl --flush; systemctl restart systemd-journald
journalctl --disk-usage
