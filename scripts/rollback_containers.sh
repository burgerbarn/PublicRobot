#!/bin/bash
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
gpasswd -d burgerbarn docker || true
apt-get remove -y $(cat /root/containers_added_packages.txt)
[ -f /root/daemon.json.before ] && cp /root/daemon.json.before /etc/docker/daemon.json || rm -f /etc/docker/daemon.json
