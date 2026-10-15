#!/bin/bash
# Stop Nav2 completely (launch SIGINT left orphaned nav2 nodes running, 2026-09-28) and disable motion.
source /opt/ros/humble/setup.bash
timeout 10 ros2 service call /rosorin_board_driver/enable std_srvs/srv/SetBool "{data: false}" >/dev/null 2>&1
pkill -INT -f "ros2 launch rosorin_base nav"            # nav.launch.py / nav_slam.launch.py
# our own nodes started by the same launch files (2026-10-05: the workspace nav2_behaviors overlay and the depth gate were
# left running after a stop - a second behaviour server / depth gate then answered the next run)
OWN="ros2_ws/install/nav2_behaviors/lib/nav2_behaviors/|lib/rosorin_base/depth_gate|lib/rosorin_base/nav_supervisor|occupancy_grid_localizer_container"
pkill -INT -f "$OWN"
pkill -INT -f "/opt/ros/humble/lib/nav2_"; pkill -INT -f "slam_toolbox_node"; pkill -INT -f "nvblox_ros/nvblox_node"
for i in $(seq 1 10); do pgrep -f "/opt/ros/humble/lib/nav2_" >/dev/null || break; sleep 0.5; done
pkill -KILL -f "$OWN"; pkill -KILL -f "/opt/ros/humble/lib/nav2_"; pkill -KILL -f "slam_toolbox_node"; pkill -KILL -f "nvblox_ros/nvblox_node"
sleep 0.5
pgrep -fa "/opt/ros/humble/lib/nav2_" || echo "nav2 stopped"
# output only: with no /cmd_vel in the graph (driver not up yet) the grep finds nothing, and as the last command it
# failed the unit's ExecStartPre/ExecStopPost (simulated rebuild 2026-10-08)
timeout 8 ros2 topic info /cmd_vel | grep "Publisher count"
exit 0
