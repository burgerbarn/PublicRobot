#!/bin/bash
# OWNER-APPROVED EXCEPTION (2026-09-28): Deptrum Aurora 930 driver = vendor ROS wrapper 0.2.11 (source) +
# closed prebuilt libdeptrum_stream_aurora900.so 1.1.22 (aarch64, built for 18.04). Copied from the factory
# partition (mounted ro at /mnt/factory). Isolated: ~/vendor_ws only (NOT ~/ros2_ws, nothing in /usr, /opt).
# System change: one udev rule for 3251:1930 only, group video, 0660 (vendor rule was 0666 for all 3251:*).
# Rollback: bash ~/setup/rollback_aurora930.sh
set -euo pipefail
SRC=/mnt/factory/home/ubuntu/third_party/aurora_ws/src/deptrum-ros-driver-aurora930-0.2.11
WS=/home/burgerbarn/vendor_ws
[ -d "$SRC" ] || { echo "factory partition not mounted at /mnt/factory"; exit 1; }
[ -e "$WS" ] && { echo "$WS exists; rollback first"; exit 1; }
mkdir -p "$WS/src"
cp -a "$SRC" "$WS/src/"
sha256sum "$WS/src/deptrum-ros-driver-aurora930-0.2.11/ext/"*/lib/libdeptrum_stream_aurora900.so
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="3251", ATTRS{idProduct}=="1930", MODE="0660", GROUP="video"' \
  | sudo tee /etc/udev/rules.d/60-rosorin-aurora930.rules >/dev/null
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=3251
set +u; source /opt/ros/humble/setup.bash; set -u
cd "$WS" && colcon build --cmake-args -DSTREAM_SDK_TYPE=AURORA930 -DCMAKE_BUILD_TYPE=Release 2>&1 | tail -5
echo AURORA930_INSTALLED
