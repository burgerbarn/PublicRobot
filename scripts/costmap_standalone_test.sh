#!/bin/bash
# standalone local costmap (nav2.yaml local_costmap) + snapshot, arm in drive pose. SRC="scan depth" | "depth" | "scan"
source /opt/ros/humble/setup.bash; cd ~/ros2_ws && source install/setup.bash
pkill -f "lib/nav2_costmap_2d/nav2_costmap_2d" ; sleep 1
SRC="${SRC:-scan depth}" python3 - <<'PY'
import os, yaml
d = yaml.safe_load(open('install/rosorin_base/share/rosorin_base/config/nav2.yaml'))
c = d['local_costmap']['local_costmap']; c['ros__parameters']['voxel_layer']['observation_sources'] = os.environ['SRC']
yaml.safe_dump({'costmap': {'costmap': c}}, open('/tmp/local_costmap_standalone.yaml', 'w'))
PY
nohup ros2 run nav2_costmap_2d nav2_costmap_2d --ros-args --params-file /tmp/local_costmap_standalone.yaml > /tmp/costmap.log 2>&1 &
sleep 5; ros2 lifecycle set /costmap/costmap configure >/dev/null; ros2 lifecycle set /costmap/costmap activate >/dev/null
cd ~/vision && . venv/bin/activate && python ~/model_check/costmap_snap.py   # copy of scripts/costmap_snap.py 2>&1 | grep costmap
cp /tmp/costmap.png "/tmp/costmap_${SRC// /_}.png"
