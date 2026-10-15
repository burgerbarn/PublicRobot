#!/bin/bash
# Can slam_toolbox continue the robot's saved map from a recorded drive without spoiling it? Off the floor: isolated
# ROS domain 77, the bag's /scan and odometry TF replayed (its own map->odom removed), stock async_slam_toolbox_node
# loading the saved pose graph and starting at the run's first pose. Nothing is written to ~/maps.
# Usage: slam_continue_replay.sh <bag dir> <pose graph without extension> "x y yaw_rad" [rate]
source /opt/ros/humble/setup.bash; source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
BAG=$1; GRAPH=$2; read X Y YAW <<< "$3"; RATE=${4:-1.5}
HERE=$(dirname "$0"); OUT=$(mktemp -d /tmp/slam_continue.XXXX)
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
trap 'kill77; sleep 1; kill77 -9' EXIT
kill77; sleep ${GAP:-20}
cat > $OUT/over.yaml <<YAML
slam_toolbox:
  ros__parameters:
    use_sim_time: true
    mode: mapping
    map_file_name: $GRAPH
YAML
if [ "$DOCK" = 1 ]; then echo "    map_start_at_dock: true" >> $OUT/over.yaml      # no pose given: start at the first node of the saved graph
else echo "    map_start_pose: [$X, $Y, $YAW]" >> $OUT/over.yaml; fi
NODE=${NODE:-async_slam_toolbox_node}      # NODE=map_and_localization_slam_toolbox_node + LOCALIZE_S=n: localize only (map untouched) for n s, then map
/opt/ros/humble/lib/slam_toolbox/$NODE --ros-args -r __node:=slam_toolbox --params-file $SHARE/config/slam.yaml --params-file $OUT/over.yaml > $OUT/slam.log 2>&1 &
SLAM=$!
python3 $HERE/slam_continue_judge.py $OUT/map > $OUT/judge.log 2>&1 &
JUDGE=$!
sleep 10
if [ -n "$LOCALIZE_S" ]; then
  timeout 15 ros2 service call /slam_toolbox/set_localization_mode std_srvs/srv/SetBool "{data: true}" | tail -1 | cut -c1-80
  ( sleep $LOCALIZE_S; timeout 15 ros2 service call /slam_toolbox/set_localization_mode std_srvs/srv/SetBool "{data: false}" | tail -1 | cut -c1-80; echo "switched to mapping after $LOCALIZE_S s" ) &
fi
ros2 bag play "$BAG" --clock 100 --rate $RATE --topics /scan /tf /tf_static --remap /tf:=/tf_bag > $OUT/play.log 2>&1
sleep 3
echo "slam_toolbox cpu $(awk '{print ($14+$15)/100}' /proc/$SLAM/stat 2>/dev/null) s, memory $(awk '/VmRSS/{print int($2/1024)}' /proc/$SLAM/status 2>/dev/null) MB, bag $(grep -a -o "Duration: *[0-9.]*" <(ros2 bag info "$BAG") | head -1)"
kill -INT $JUDGE; wait $JUDGE 2>/dev/null
tail -1 $OUT/judge.log
grep -a -i -E "error|fail|exception|deserializ|loaded|pose graph" $OUT/slam.log | sed 's/\x1b\[[0-9;]*m//g' | sed -E 's/\[[0-9.]+\] //' | sort | uniq -c | sort -rn | head -6 | cut -c1-200
echo "pictures and logs: $OUT"
