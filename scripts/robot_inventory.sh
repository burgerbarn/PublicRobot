#!/bin/bash
# Read-only inventory of a ROSOrin Pro host. Safe for set -u ROS setups.
set +u
export LANG=C
echo "=====BEGIN_INVENTORY====="
echo "=====META====="
date -u +"%Y-%m-%dT%H:%M:%SZ"
echo "USER=$(whoami)"
echo "HOSTNAME=$(hostname)"
echo "PWD=$(pwd)"
echo "=====OS====="
uname -a
cat /etc/os-release 2>/dev/null || true
cat /etc/nv_tegra_release 2>/dev/null || true
if [ -f /proc/device-tree/model ]; then
  echo "DT_MODEL=$(tr -d '\0' </proc/device-tree/model)"
fi
if command -v jetson_release >/dev/null 2>&1; then
  jetson_release
fi
echo "=====CPU_MEM====="
nproc
lscpu 2>/dev/null | sed -n '1,50p'
echo "-----FREE-----"
free -h
echo "=====DISK====="
df -hT
echo "-----LSBLK-----"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null || lsblk
echo "=====NETWORK====="
hostname -I 2>/dev/null || true
ip -br addr 2>/dev/null || ifconfig -a
echo "-----ROUTE-----"
ip route 2>/dev/null || true
echo "-----WIFI-----"
nmcli -t -f NAME,TYPE,DEVICE,STATE con show --active 2>/dev/null || true
iwgetid -r 2>/dev/null || true
echo "=====USB====="
lsusb 2>/dev/null || echo "NO_LSUSB"
echo "=====PCI====="
lspci 2>/dev/null | head -80 || echo "NO_LSPCI"
echo "=====SERIAL_BY_ID====="
ls -l /dev/serial/by-id 2>/dev/null || echo "NO_BY_ID"
echo "=====SERIAL_BY_PATH====="
ls -l /dev/serial/by-path 2>/dev/null || echo "NO_BY_PATH"
echo "=====TTY====="
ls -l /dev/ttyUSB* /dev/ttyACM* /dev/ttyTHS* /dev/ttyS* 2>/dev/null || echo "NO_TTY_MATCH"
echo "=====V4L====="
ls -l /dev/video* 2>/dev/null || echo "NO_VIDEO"
command -v v4l2-ctl >/dev/null 2>&1 && v4l2-ctl --list-devices || echo "NO_V4L2CTL"
echo "=====I2C====="
ls -l /dev/i2c-* 2>/dev/null || echo "NO_I2C"
echo "=====GPIO_PWM====="
ls /sys/class/pwm 2>/dev/null || true
echo "=====AUDIO====="
aplay -l 2>/dev/null || echo "NO_APLAY"
arecord -l 2>/dev/null || echo "NO_ARECORD"
echo "=====DISPLAY====="
echo "DISPLAY=${DISPLAY:-unset}"
xrandr --query 2>/dev/null | head -40 || echo "NO_XRANDR"
ls /sys/class/drm 2>/dev/null || true
echo "=====KERNEL_MODS====="
lsmod 2>/dev/null | egrep -i 'uvc|vide|snd|spi|i2c|can|nvme|tegra|hid' | head -80
echo "=====ROS====="
echo "ROS_DISTRO=${ROS_DISTRO:-unset}"
echo "ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-unset}"
ls -d /opt/ros/* 2>/dev/null || echo "NO_OPT_ROS"
ls -d /home/ubuntu/ros* /home/ubuntu/*ws* /home/ubuntu/hiwonder* 2>/dev/null || true
echo "-----BASHRC_ROS-----"
grep -n -iE 'ros|source |colcon|ROS_' /home/ubuntu/.bashrc 2>/dev/null | head -40
echo "=====HOME_TOP====="
ls -la /home/ubuntu 2>/dev/null | head -80
echo "=====WORKSPACES====="
find /home/ubuntu -maxdepth 3 -type d \( -name 'src' -o -name 'install' -o -name 'log' -o -name 'build' \) 2>/dev/null | head -80
echo "=====PACKAGES_SRC====="
find /home/ubuntu -maxdepth 5 -name 'package.xml' 2>/dev/null | head -120
echo "=====SYSTEMD====="
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -60
echo "=====DOCKER====="
command -v docker >/dev/null 2>&1 && docker ps -a || echo "NO_DOCKER"
echo "=====PYTHON====="
python3 --version 2>/dev/null
which python3
echo "=====APT_ROSISH====="
dpkg -l 2>/dev/null | egrep -i 'ros-|jetson|nvidia|v4l|opencv' | awk '{print $2, $3}' | head -80
echo "=====END_INVENTORY====="
