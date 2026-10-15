#!/bin/bash
# "Come check this out" (rosorin-task@come): client of the always-on navigation -> behavior/come_here.py.
# Wheels are disabled on every exit path (explorer finally + nav_stop.sh). Log: ~/explore/<run>/.
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
# navigation is always on (rosorin-nav.service): wait until it is ready, never stop it
trap 'timeout 10 ros2 service call /rosorin_board_driver/enable std_srvs/srv/SetBool "{data: false}" >/dev/null 2>&1' EXIT
systemctl is-active --quiet rosorin-nav || sudo systemctl start rosorin-nav
ros2 run rosorin_base nav_wait 240 || { echo "navigation is not ready - not driving"; exit 1; }
source /home/burgerbarn/vision/venv/bin/activate
python /home/burgerbarn/behavior/come_here.py "$@"
