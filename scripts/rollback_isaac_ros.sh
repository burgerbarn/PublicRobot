#!/bin/bash
# Undo install_isaac_ros.sh: remove the Isaac ROS packages and the NVIDIA apt source.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
apt-get remove -y 'ros-humble-isaac-ros-*' 'ros-humble-nvblox-*' || true
apt-get autoremove -y
rm -f /etc/apt/sources.list.d/isaac-ros.list /usr/share/keyrings/isaac-ros.gpg
apt-get update
sudo -u burgerbarn pip3 uninstall -y cuvslam || true
