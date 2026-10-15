#!/usr/bin/env bash
# Simulated rebuild: start the physical world on bigbuddy (container lbsim-gz, network lbsim, ROS domain 99 local).
# Usage on bigbuddy: ~/lbsim/gz/start.sh      Stop: docker rm -f lbsim-gz
set -e
cd ~/lbsim/gz
docker network inspect lbsim >/dev/null 2>&1 || docker network create --subnet 172.30.0.0/24 lbsim >/dev/null
docker rm -f lbsim-gz >/dev/null 2>&1 || true
docker run -d --name lbsim-gz --network lbsim --ip 172.30.0.20 -e LBSIM_ROBOT=172.30.0.10 -e ROS_DOMAIN_ID=99 -e ROS_LOCALHOST_ONLY=1 \
  -v ~/lbsim/gz:/sim:z lbsim-gz:humble bash -c '
source /opt/ros/humble/setup.bash
ign gazebo -s -r --headless-rendering /sim/room.sdf > /sim/gazebo.log 2>&1 &
sleep 8
ros2 run ros_gz_bridge parameter_bridge /sim/lidar@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan \
  /model/rosorin/cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist > /sim/bridge.log 2>&1 &
exec python3 /sim/gz_relay.py' >/dev/null
echo "lbsim-gz started"
