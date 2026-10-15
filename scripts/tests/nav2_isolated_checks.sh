#!/bin/bash
# Checks the installed Nav2 behaviour server + collision monitor against a fake costmap / fake scan on an ISOLATED
# ROS domain (77, localhost only): nothing here can reach the base driver (domain 0). Uses the robot's deployed
# nav2.yaml (behavior_server section) and collision_monitor.yaml. Usage: nav2_isolated_checks.sh [teleop] [spin] [cm]
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
T=$(mktemp -d /tmp/nav2_iso.XXXX)
python3 - "$SHARE/config/nav2.yaml" "$T/behavior.yaml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
yaml.safe_dump({'behavior_server': d['behavior_server']}, open(sys.argv[2], 'w'))
PY
# every process of this test carries ROS_DOMAIN_ID=77 in its environment: that is how leftovers are found and removed
# (`ros2 run` wrappers do not pass a kill on to the node they started)
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
cleanup() { kill77; sleep 1; kill77 -9; rm -rf "$T"; }
trap cleanup EXIT
kill77; sleep 1
cp ${CM_PARAMS:-$SHARE/config/collision_monitor.yaml} $T/cm.yaml
/opt/ros/humble/lib/nav2_collision_monitor/collision_monitor --ros-args --params-file $T/cm.yaml > $T/cm.log 2>&1 &
$(ros2 pkg prefix nav2_behaviors)/lib/nav2_behaviors/behavior_server --ros-args --params-file $T/behavior.yaml -r cmd_vel:=cmd_vel_nav > $T/behavior.log 2>&1 &
/opt/ros/humble/lib/nav2_lifecycle_manager/lifecycle_manager --ros-args -p autostart:=true \
  -p node_names:="['behavior_server','collision_monitor']" -p bond_timeout:=0.0 > $T/lm.log 2>&1 &
python3 "$(dirname "$0")/nav2_isolated_checks.py" "$@"
RC=$?
[ -n "$CM_LOG" ] && { echo "--- collision monitor log"; sed 's/\x1b\[[0-9;]*m//g' $T/cm.log | grep -v "Ignoring the source" | cut -c1-200 | tail -${CM_LOG}; }
echo "--- behavior_server library: $(ros2 pkg prefix nav2_behaviors)"
grep -E "Collision Ahead|collided|approaching|Ignoring the source|ERROR|FATAL" $T/behavior.log $T/cm.log | sed 's/\x1b\[[0-9;]*m//g' | sort | uniq -c | sort -rn | cut -c1-220 | head -12
exit $RC
