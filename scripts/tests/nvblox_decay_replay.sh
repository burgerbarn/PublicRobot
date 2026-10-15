#!/bin/bash
# How long does nvblox remember what the camera no longer sees? Replays a RECORDED run (depth + TF from a bag) into
# nvblox on an isolated ROS domain (77, localhost only; nothing reaches the base) once per decay setting and records
# the obstacle slice Nav2 would get. Analysis: nvblox_decay_analyze.py. docs/research/nvblox_reference.md.
# Usage: nvblox_decay_replay.sh <bag dir> <out dir> [name=factor ...]   factor = tsdf_decay_factor at 5 Hz, 0 = no decay
#   default settings: stock=0.95 slow=0.99 none=0
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
BAG=$1; OUT=$2; shift 2
[ -d "$BAG" ] && [ -n "$OUT" ] || { echo "usage: $0 <bag dir> <out dir> [name=factor ...]"; exit 2; }
SETTINGS="${*:-stock=0.95 slow=0.99 none=0}"
# GATE=1: depth goes through rosorin_base depth_gate first (as in the navigation launch files since 2026-10-02)
DEPTH=/aurora/depth/image_raw; [ "$GATE" = 1 ] && DEPTH=/depth_gate/depth
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
HERE=$(dirname "$0")
mkdir -p "$OUT"
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
trap 'kill77; sleep 1; kill77 -9' EXIT
for S in $SETTINGS; do
  NAME=${S%%=*}; F=${S##*=}
  kill77; sleep 2
  if [ "$F" = 0 ]; then RATE=0.0; FACT=0.95; else RATE=5.0; FACT=$F; fi
  cat > $OUT/override_$NAME.yaml <<EOF
/**:
  ros__parameters:
    use_sim_time: true
    decay_tsdf_rate_hz: $RATE
    static_mapper:
      tsdf_decay_factor: $FACT
EOF
  /opt/ros/humble/lib/nvblox_ros/nvblox_node --ros-args -r __node:=nvblox_node \
    --params-file /opt/ros/humble/share/nvblox_examples_bringup/config/nvblox/nvblox_base.yaml \
    --params-file $SHARE/config/nvblox.yaml --params-file $OUT/override_$NAME.yaml \
    -r camera_0/depth/image:=$DEPTH -r camera_0/depth/camera_info:=/aurora/rgb/camera_info \
    > $OUT/nvblox_$NAME.log 2>&1 &
  [ "$GATE" = 1 ] && $(ros2 pkg prefix rosorin_base)/lib/rosorin_base/depth_gate --ros-args -p use_sim_time:=true \
    -r depth_in:=/aurora/depth/image_raw > $OUT/gate_$NAME.log 2>&1 &
  python3 $HERE/nvblox_slice_recorder.py $OUT/slices_$NAME.npz > $OUT/recorder_$NAME.log 2>&1 &
  REC=$!
  sleep 8
  ros2 bag play "$BAG" --clock 100 --topics /tf /tf_static /aurora/depth/image_raw /aurora/rgb/camera_info /joint_states \
    > $OUT/play_$NAME.log 2>&1
  sleep 2; kill -INT $REC; wait $REC 2>/dev/null
  echo "$NAME (factor $F): $(tail -1 $OUT/recorder_$NAME.log) $([ "$GATE" = 1 ] && grep -h "held back" $OUT/gate_$NAME.log | sed 's/.*depth frames: //' | tr '\n' ';')"
done
