#!/bin/bash
# CPU cost of cuVSLAM (vslam_odom) per setting, on a RECORDED run replayed on the isolated domain 77: nothing reaches the
# base. Stops rosorin-vslam during the test (so two trackers do not compete) and starts it again at the end.
# Usage: vslam_cpu_replay.sh <bag dir> [seconds] ; settings are the ones listed in SETTINGS below.
source /opt/ros/humble/setup.bash; source /home/burgerbarn/ros2_ws/install/setup.bash
BAG=$1; SECS=${2:-40}
[ -d "$BAG" ] || { echo "usage: $0 <bag dir> [seconds]"; exit 2; }
S=$(timeout 5 ros2 topic echo --once --field data /rosorin_board_driver/state 2>/dev/null | grep -o '"enabled": [a-z]*'); [ "$S" = '"enabled": false' ] || { echo "wheels enabled - not now"; exit 2; }
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
trap 'kill77; sleep 1; kill77 -9; sudo systemctl start rosorin-vslam' EXIT
sudo systemctl stop rosorin-vslam; sleep 2
for CFG in "async_sba:=true" "async_sba:=false"; do
  kill77; sleep 2
  $(ros2 pkg prefix rosorin_base)/lib/rosorin_base/vslam_odom --ros-args -p $CFG > /tmp/vslam_$CFG.log 2>&1 &
  ros2 bag play "$BAG" --loop --topics /tf /tf_static /joint_states /aurora/rgb/camera_info /aurora/rgb/image_raw /aurora/depth/image_raw > /tmp/vslam_play.log 2>&1 &
  PLAY=$!
  sleep 8
  P=$(pgrep -f "lib/rosorin_base/[v]slam_odom" | head -1)
  echo "--- $CFG"
  top -H -b -d 4 -n $((SECS/4+1)) -p $P | awk '/^top -/{n++} n>=2 && $1 ~ /^[0-9]+$/ {c[$1]+=$9} END{t=0; for(x in c){v=c[x]/(n-1); t+=v; if(v>5) printf "  thread %s %5.0f %%\n", x, v}; printf "  process total %5.0f %% of one core\n", t}'
  timeout 8 ros2 topic hz /odom_vslam 2>&1 | grep "average rate" | tail -1 | sed 's/^/  /'
  kill $PLAY 2>/dev/null; wait $PLAY 2>/dev/null
done
