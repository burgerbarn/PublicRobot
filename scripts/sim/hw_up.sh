#!/usr/bin/env bash
# Simulated rebuild: "power on" the robot's emulated hardware inside lbsim-robot (again after a robot reboot).
# Board -> /dev/ttyACM0 (wheel commands go to Gazebo at 172.30.0.20:5602); LiDAR -> /dev/lidar (scans from Gazebo).
set -euo pipefail
# LAN cut-off: the simulated robot must never reach the real robot, bigbuddy's Buddy services or server
# (its software calls them by 192.168.1.x address). Internet (apt, pip) still goes out via 172.30.0.1.
docker exec lbsim-robot ip route replace prohibit 192.168.0.0/16
docker exec lbsim-robot bash /opt/lbsim/start_hw.sh
docker exec lbsim-robot bash -c 'ip route | grep -E "prohibit|default"'
# the Jetson kernel name for uname -r (idempotent; survives restarts anyway, needed after a rebuild of the container)
~/lbsim/bin/robot_kernel_name.sh >/dev/null
