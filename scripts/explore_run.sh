#!/bin/bash
# One self-run room exploration (rosorin-explore.service) or body practice (PRACTICE=1, rosorin-practice.service):
#   kept map (~/maps/room_live.yaml) -> client of the always-on navigation
#   no kept map yet, or REMAP=1      -> MAPPING run: slam_toolbox + Nav2 + nvblox, map saved with nav2 map_saver
# then behavior/explore_auto.py drives (explore_lite frontiers, then floor not driven over), back to the start.
# Wheels are disabled on every exit path (explorer finally + nav_stop.sh). Log: ~/explore/<run>/.
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
M=/home/burgerbarn/maps
# Navigation is ALWAYS ON (rosorin-nav.service: kept map, AMCL, Nav2, nvblox, kept usable by nav_supervisor). A run is a
# client: it waits for "ready" and never stops navigation. Only a MAPPING run (no kept map yet, or REMAP=1) needs the
# other stack (slam_toolbox): it stops the service, maps, and starts the service again on every exit path.
MAPPING=0
[ "$PRACTICE" = 1 ] && export ROSORIN_PRACTICE=1          # body practice uses the running navigation's behaviour server
{ [ ! -f $M/room_live.yaml ] || [ "$REMAP" = 1 ]; } && MAPPING=1
BAG=/home/burgerbarn/bags/$(date +%Y%m%d_%H%M%S)
finish() {
  [ -n "$BAGPID" ] && { kill -INT $BAGPID 2>/dev/null; wait $BAGPID 2>/dev/null; }
  ls -dt /home/burgerbarn/bags/*/ 2>/dev/null | tail -n +7 | while read d; do rm -rf "$d"; done   # keep the 6 newest runs
  ros2 param set /rosorin_board_driver drive_pan -- -1 >/dev/null 2>&1   # back to the owner's setting (pan stays put)
  pkill -INT -f 'explore_lite/explore' 2>/dev/null
  timeout 10 ros2 service call /rosorin_board_driver/enable std_srvs/srv/SetBool "{data: false}" >/dev/null 2>&1
  if [ $MAPPING = 1 ]; then bash /home/burgerbarn/setup/nav_stop.sh; sudo systemctl start --no-block rosorin-nav; fi
  sudo systemctl start --no-block rosorin-selfcheck      # after every drive the robot looks at itself (selfcare/)
}
trap finish EXIT
# background self-work yields to driving (2026-10-05: face training started at boot and ran through the first floor run)
sudo systemctl stop --no-block rosorin-selfcheck rosorin-owner-faces rosorin-reappear 2>/dev/null
# camera straight ahead while navigating: the depth camera maps the floor the robot drives onto (PLAN M1)
ros2 param set /rosorin_board_driver drive_pan 500 >/dev/null 2>&1
if [ $MAPPING = 1 ]; then
  sudo systemctl stop rosorin-nav
  ros2 launch rosorin_base nav_slam.launch.py > /tmp/explore_nav.log 2>&1 &
  for i in $(seq 1 60); do ros2 action list 2>/dev/null | grep -q '^/navigate_to_pose$' && break; sleep 1; done
  ros2 action list | grep -q '^/navigate_to_pose$' || { echo "nav2 did not come up"; exit 1; }
  sleep 8                                     # lifecycle: let costmaps publish
else
  systemctl is-active --quiet rosorin-nav || sudo systemctl start rosorin-nav
  ros2 run rosorin_base nav_wait 240 || { echo "navigation is not ready - not driving"; exit 1; }
fi
# record the run (built-in ros2 bag): what it sensed, commanded and measured - to replay failures off the floor and as
# the robot's own training data (docs/research/training_the_body.md step 0)
mkdir -p /home/burgerbarn/bags
ros2 bag record -o $BAG --compression-mode file --compression-format zstd --max-bag-size 1500000000 \
  /scan /imu/data_raw /battery /tf /tf_static /joint_states \
  /aurora/rgb/image_raw /aurora/rgb/camera_info /aurora/depth/image_raw \
  /cmd_vel /cmd_vel_nav /wheel/odom /odometry/filtered /odom_vslam /odom_rf2o /estop /rosorin_board_driver/state \
  /rosorin_board_driver/arm_busy \
  /plan /local_plan /map /amcl_pose /local_costmap/costmap /global_costmap/costmap /nvblox_node/static_map_slice \
  > /tmp/explore_bag.log 2>&1 &
BAGPID=$!
source /home/burgerbarn/vision/venv/bin/activate
if [ "$PRACTICE" = 1 ]; then
  python /home/burgerbarn/behavior/practice.py; /usr/bin/python3 /home/burgerbarn/behavior/learn_body.py; RC=3
else
  python /home/burgerbarn/behavior/explore_auto.py "$@"; RC=$?
fi
deactivate
# keep the new map only from a run that finished and got home (a run cut short leaves a partial map); old one -> history
if [ $MAPPING = 1 ] && [ $RC = 0 ]; then
  [ -f $M/room_live.yaml ] && { H=$M/history/$(date +%Y%m%d_%H%M%S); mkdir -p $H && mv $M/room_live.* $H/; }
  ros2 run nav2_map_server map_saver_cli --fmt png -f $M/room_live 2>&1 | tail -1
  ls -la $M/room_live.*
fi
exit $RC
