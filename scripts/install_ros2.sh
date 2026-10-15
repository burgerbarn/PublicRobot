#!/bin/bash
# ROS 2 Humble (ros-base + dev tools) per docs.ros.org Humble "Ubuntu (deb packages)".
# L4T packages held at the installed version (golden image baseline). Run: sudo bash ~/setup/install_ros2.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
L4T_BEFORE=$(head -1 /etc/nv_tegra_release)

echo "== hold L4T packages"
# only installed packages (dpkg also lists known-but-not-installed ones, e.g. nvidia-l4t-ccp-generic)
apt-mark hold $(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 'nvidia-l4t-*' | awk '$1 ~ /^[ih]i$/ {print $2}') >/dev/null
echo "held: $(apt-mark showhold | grep -c '^nvidia-l4t-')"

echo "== apt update + upgrade (Ubuntu packages; L4T held)"
apt-get update -q
apt-get -y -q upgrade

echo "== ros2-apt-source"
apt-get install -y -q curl
V=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F '"tag_name"' | awk -F'"' '{print $4}')
[ -n "$V" ] || { echo "could not read ros-apt-source version"; exit 1; }
CODENAME=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME}}")
curl -fL -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${V}/ros2-apt-source_${V}.${CODENAME}_all.deb"
dpkg -i /tmp/ros2-apt-source.deb

echo "== install ros-humble-ros-base + ros-dev-tools"
apt-get update -q
apt-get install -y -q ros-humble-ros-base ros-dev-tools

echo "== verify"
dpkg-query -W -f='${Package} ${Version}\n' ros-humble-ros-base ros-dev-tools ros2-apt-source
L4T_AFTER=$(head -1 /etc/nv_tegra_release)
[ "$L4T_BEFORE" = "$L4T_AFTER" ] && echo "L4T unchanged: $L4T_AFTER" || echo "WARNING L4T CHANGED: $L4T_AFTER"
bash -c 'source /opt/ros/humble/setup.bash && ros2 --help >/dev/null && echo "ros2 CLI OK" && printenv ROS_DISTRO'
systemctl --failed --no-legend
echo "ROS2_DONE"
