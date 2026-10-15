#!/bin/bash
# Extracts the nvblox slice that was RECORDED LIVE in a bag (with the robot pose) into slices_live.npz, by playing the
# bag's slice + TF on the isolated domain into nvblox_slice_recorder.py. Usage: nvblox_live_slice.sh <bag dir> <out dir>
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
mkdir -p "$2"
python3 $(dirname "$0")/nvblox_slice_recorder.py $2/slices_live.npz > $2/recorder_live.log 2>&1 &
REC=$!
sleep 4
ros2 bag play "$1" --clock 100 --topics /tf /tf_static /aurora/rgb/camera_info /nvblox_node/static_map_slice > $2/play_live.log 2>&1
sleep 2; kill -INT $REC; wait $REC 2>/dev/null
tail -1 $2/recorder_live.log
