#!/bin/bash
# AMCL settings against a RECORDED run on the kept map, off the floor: isolated ROS domain 77 (localhost only), the
# bag's /scan and odometry TF replayed, the bag's own map->odom removed, a fresh AMCL per setting started at the run's
# first pose. Per setting: how well AMCL's poses explain the scans (nav_supervisor's scan fit and beams through walls)
# and the CPU time AMCL used. Usage: amcl_replay.sh <bag dir> <map.yaml> "x y yaw_rad" [rate] ; settings below.
source /opt/ros/humble/setup.bash; source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
BAG=$1; MAP=$2; read X Y YAW <<< "$3"; RATE=${4:-2.0}
HERE=$(dirname "$0"); OUT=$(mktemp -d /tmp/amcl_replay.XXXX)
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
trap 'kill77; sleep 1; kill77 -9' EXIT
declare -A SET
SET[deployed_beam360]="{}"
SET[likelihood_60]="{laser_model_type: likelihood_field, max_beams: 60}"
SET[likelihood_180]="{laser_model_type: likelihood_field, max_beams: 180}"
SET[beam360_recovery]="{recovery_alpha_slow: 0.001, recovery_alpha_fast: 0.1}"
SET[likelihood_180_recovery]="{laser_model_type: likelihood_field, max_beams: 180, recovery_alpha_slow: 0.001, recovery_alpha_fast: 0.1}"
for NAME in ${ORDER:-deployed_beam360 likelihood_60 likelihood_180 beam360_recovery likelihood_180_recovery}; do
  kill77; sleep ${GAP:-2}      # GAP: seconds between one stack being killed and the next starting
  python3 - "$SHARE/config/nav2.yaml" "$OUT/amcl_$NAME.yaml" "${SET[$NAME]}" $X $Y $YAW <<'PY'
import sys, yaml
p = yaml.safe_load(open(sys.argv[1]))['amcl']['ros__parameters']
p.update(yaml.safe_load(sys.argv[3])); p.update(use_sim_time=True, set_initial_pose=True,
    initial_pose=dict(x=float(sys.argv[4]), y=float(sys.argv[5]), z=0.0, yaw=float(sys.argv[6])))
yaml.safe_dump({'amcl': {'ros__parameters': p}, 'map_server': {'ros__parameters': {'use_sim_time': True}}}, open(sys.argv[2], 'w'))
PY
  /opt/ros/humble/lib/nav2_map_server/map_server --ros-args -r __node:=map_server --params-file $OUT/amcl_$NAME.yaml -p yaml_filename:=$MAP > $OUT/map_$NAME.log 2>&1 &
  /opt/ros/humble/lib/nav2_amcl/amcl --ros-args -r __node:=amcl --params-file $OUT/amcl_$NAME.yaml > $OUT/amcl_$NAME.log 2>&1 &
  AMCL=$!
  /opt/ros/humble/lib/nav2_lifecycle_manager/lifecycle_manager --ros-args -r __node:=lm -p use_sim_time:=true -p autostart:=true -p node_names:="[map_server, amcl]" -p bond_timeout:=0.0 > $OUT/lm_$NAME.log 2>&1 &
  python3 $HERE/amcl_replay_judge.py $OUT/judge_$NAME.json > $OUT/judge_$NAME.log 2>&1 &
  JUDGE=$!
  sleep 6
  ros2 bag play "$BAG" --clock 100 --rate $RATE --topics /scan /tf /tf_static --remap /tf:=/tf_bag > $OUT/play_$NAME.log 2>&1
  sleep 1
  CPU=$(awk '{print ($14+$15)/100}' /proc/$AMCL/stat 2>/dev/null)
  kill -INT $JUDGE; wait $JUDGE 2>/dev/null
  echo "$NAME: amcl cpu ${CPU:-?} s | $(tail -1 $OUT/judge_$NAME.log)"
done
echo "logs: $OUT"
