#!/bin/bash
# Does the Nav2 stack come up with the DEPLOYED nav2.yaml + behaviour tree? Isolated ROS domain (77, localhost only),
# fake TF, no sensors, no base: nothing here can reach the wheels. bt_navigator loads the tree when it activates, so
# "bt_navigator active" = the tree parsed and every node type / action server it names exists.
# Usage: nav2_bringup_check.sh   (exit 0 = all five servers active)
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
export ROS_DOMAIN_ID=77 ROS_LOCALHOST_ONLY=1
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
T=$(mktemp -d /tmp/nav2_iso.XXXX)
kill77() { for e in /proc/[0-9]*/environ; do p=${e#/proc/}; p=${p%/environ}
  [ "$p" != $$ ] && [ "$p" != "$BASHPID" ] && grep -qz '^ROS_DOMAIN_ID=77$' $e 2>/dev/null && kill $1 $p 2>/dev/null; done; true; }
cleanup() { kill77; sleep 1; kill77 -9; rm -rf "$T"; }
trap cleanup EXIT
kill77; sleep 1
python3 - "$SHARE/config/nav2.yaml" "$T/nav2.yaml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
gc = d['global_costmap']['global_costmap']['ros__parameters']       # as nav_slam.launch.py: no keepout on a new map
gc.pop('filters', None); gc.pop('keepout_filter', None)
yaml.safe_dump(d, open(sys.argv[2], 'w'))
PY
L=/opt/ros/humble/lib
B=$(ros2 pkg prefix nav2_behaviors)/lib/nav2_behaviors/behavior_server
$L/nav2_controller/controller_server --ros-args --params-file $T/nav2.yaml -r cmd_vel:=cmd_vel_nav > $T/controller.log 2>&1 &
$L/nav2_smoother/smoother_server --ros-args --params-file $T/nav2.yaml > $T/smoother.log 2>&1 &
$L/nav2_planner/planner_server --ros-args --params-file $T/nav2.yaml > $T/planner.log 2>&1 &
$B --ros-args --params-file $T/nav2.yaml -r cmd_vel:=cmd_vel_nav > $T/behavior.log 2>&1 &
$L/nav2_bt_navigator/bt_navigator --ros-args --params-file $T/nav2.yaml > $T/bt.log 2>&1 &
$L/nav2_lifecycle_manager/lifecycle_manager --ros-args -p autostart:=true -p bond_timeout:=0.0 \
  -p node_names:="['controller_server','smoother_server','planner_server','behavior_server','bt_navigator']" > $T/lm.log 2>&1 &
python3 - <<'PY'
import math, sys, time
import rclpy
from geometry_msgs.msg import TransformStamped
from lifecycle_msgs.srv import GetState
from tf2_ros import StaticTransformBroadcaster
rclpy.init(); n = rclpy.create_node('bringup_check'); tf = StaticTransformBroadcaster(n); ts = []
for parent, child, x, yaw in (('map', 'odom', 0.0, 0.0), ('odom', 'base_link', 0.0, 0.0), ('base_link', 'laser', 0.048, math.pi)):
    t = TransformStamped(); t.header.stamp = n.get_clock().now().to_msg(); t.header.frame_id, t.child_frame_id = parent, child
    t.transform.translation.x = x; t.transform.rotation.z, t.transform.rotation.w = math.sin(yaw / 2), math.cos(yaw / 2); ts.append(t)
tf.sendTransform(ts)
names = ['controller_server', 'smoother_server', 'planner_server', 'behavior_server', 'bt_navigator']
cl = {k: n.create_client(GetState, f'/{k}/get_state') for k in names}
state, end = {}, time.time() + 90
while time.time() < end and not all(state.get(k) == 'active' for k in names):
    for k, c in cl.items():
        if c.service_is_ready():
            f = c.call_async(GetState.Request()); rclpy.spin_until_future_complete(n, f, timeout_sec=2)
            if f.result() is not None:
                state[k] = f.result().current_state.label
    rclpy.spin_once(n, timeout_sec=0.5)
for k in names:
    print(f'{k:20s} {state.get(k)}')
sys.exit(0 if all(state.get(k) == 'active' for k in names) else 1)
PY
RC=$?
echo "--- tree: $(grep -o 'default_nav_to_pose_bt_xml:.*' $T/nav2.yaml)"
grep -hE "ERROR|FATAL|Exception|Failed" $T/*.log | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-200 | sort | uniq -c | head -8
exit $RC
