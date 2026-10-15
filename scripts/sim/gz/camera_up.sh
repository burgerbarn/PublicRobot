#!/usr/bin/env bash
# Simulated rebuild: switch the simulated Aurora 930 on (from chapter 15 on): Gazebo's camera into the robot's ROS
# graph (domain 0, network lbsim) as the real driver's /aurora topics. On bigbuddy: ~/lbsim/gz/camera_up.sh
# Off: docker exec lbsim-gz pkill -f '[s]im_aurora.py|[_]lbsim/camera'
set -euo pipefail
docker exec lbsim-gz bash -c "pkill -f '[s]im_aurora.py|[_]lbsim/camera' || true"
docker exec -d -e ROS_DOMAIN_ID=0 -e ROS_LOCALHOST_ONLY=0 lbsim-gz bash -c 'source /opt/ros/humble/setup.bash
ros2 run ros_gz_bridge parameter_bridge /sim/camera/image@sensor_msgs/msg/Image[ignition.msgs.Image \
  /sim/camera/depth_image@sensor_msgs/msg/Image[ignition.msgs.Image /sim/camera/camera_info@sensor_msgs/msg/CameraInfo[ignition.msgs.CameraInfo \
  --ros-args -r /sim/camera/image:=/_lbsim/camera/image -r /sim/camera/depth_image:=/_lbsim/camera/depth_image \
  -r /sim/camera/camera_info:=/_lbsim/camera/camera_info > /sim/camera_bridge.log 2>&1 &
exec python3 /sim/sim_aurora.py > /sim/sim_aurora.log 2>&1'
echo "simulated Aurora 930 started"
