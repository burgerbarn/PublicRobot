#!/bin/bash
# What would `track_unknown_space: true` on the LOCAL costmap do to this robot, standing where it is now?
# Runs a standalone local costmap (nav2.yaml local_costmap, LiDAR layer + inflation; nvblox layer left out: its node
# only runs inside navigation) on the live scan and TF - it publishes costmap topics only, no command topics - once
# with unknown space untracked (as deployed) and once tracked, and tests the footprint outline the way DWB's
# ObstacleFootprint critic and the Nav2 behaviours do (LETHAL 254 or NO_INFORMATION 255 on the outline = refused):
# in-place turns in 5 deg steps, straight ahead in 5 cm steps, sideways. docs/research/nav2_reference.md 21-22.
# Navigation must NOT be running (it owns /local_costmap; this uses /costmap).
source /opt/ros/humble/setup.bash
source /home/burgerbarn/ros2_ws/install/setup.bash
systemctl is-active --quiet rosorin-nav && { echo "navigation is running - not now"; exit 2; }
SHARE=$(ros2 pkg prefix rosorin_base)/share/rosorin_base
T=$(mktemp -d /tmp/unk.XXXX)
trap '[ -n "$PID" ] && kill $PID 2>/dev/null; rm -rf $T' EXIT
for TRACK in false true; do
  python3 - "$SHARE/config/nav2.yaml" "$T/costmap.yaml" $TRACK <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
c = d['local_costmap']['local_costmap']['ros__parameters']
c['plugins'] = ['voxel_layer', 'inflation_layer']; c.pop('nvblox_layer', None)
c['track_unknown_space'] = sys.argv[3] == 'true'
yaml.safe_dump({'costmap': {'costmap': {'ros__parameters': c}}}, open(sys.argv[2], 'w'))
PY
  /opt/ros/humble/lib/nav2_costmap_2d/nav2_costmap_2d --ros-args --params-file $T/costmap.yaml > $T/costmap_$TRACK.log 2>&1 &
  PID=$!
  sleep 4
  timeout 20 ros2 lifecycle set /costmap/costmap configure > /dev/null 2>&1
  timeout 20 ros2 lifecycle set /costmap/costmap activate > /dev/null 2>&1
  python3 - $TRACK <<'PY'
import math, sys, time
import numpy as np
import rclpy
from nav2_msgs.msg import Costmap
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener

FOOT = [(0.165, 0.13), (0.165, -0.125), (-0.16, -0.125), (-0.16, 0.13)]       # nav2.yaml footprint + padding 0.01
rclpy.init(); n = rclpy.create_node('unknown_space_check'); buf = Buffer(); TransformListener(buf, n); got = {}
qos = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
n.create_subscription(Costmap, '/costmap/costmap_raw', lambda m: got.__setitem__('m', m), qos)
end = time.time() + 12
while time.time() < end:
    rclpy.spin_once(n, timeout_sec=0.1)
m = got.get('m')
try:
    tr = buf.lookup_transform('odom', 'base_link', Time()).transform
except Exception as e:
    tr = None
if m is None or tr is None:
    print(f'track_unknown_space {sys.argv[1]}: no costmap or no pose'); sys.exit(1)
W, H, res = m.metadata.size_x, m.metadata.size_y, m.metadata.resolution
g = np.array(m.data, np.uint8).reshape(H, W)
ox, oy = m.metadata.origin.position.x, m.metadata.origin.position.y
q = tr.rotation; yaw0 = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
x0, y0 = tr.translation.x, tr.translation.y


def outline_cost(dx, dy, dyaw):
    """Highest cell cost on the footprint outline for the robot moved by (dx, dy) in its own frame and turned dyaw."""
    c0, s0 = math.cos(yaw0), math.sin(yaw0)
    px, py, yaw = x0 + c0 * dx - s0 * dy, y0 + s0 * dx + c0 * dy, yaw0 + dyaw
    worst = 0
    pts = [(px + math.cos(yaw) * fx - math.sin(yaw) * fy, py + math.sin(yaw) * fx + math.cos(yaw) * fy) for fx, fy in FOOT]
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        for k in np.linspace(0.0, 1.0, int(math.hypot(bx - ax, by - ay) / (res / 2)) + 2):
            i, j = int((ax + (bx - ax) * k - ox) / res), int((ay + (by - ay) * k - oy) / res)
            if not (0 <= i < W and 0 <= j < H):
                return 254
            worst = max(worst, int(g[j, i]))
    return worst


def ok(c):
    return c not in (254, 255)


turns = {d: outline_cost(0, 0, math.radians(d)) for d in range(-90, 91, 5)}
ahead = {round(d, 2): outline_cost(d, 0, 0) for d in np.arange(0.05, 0.55, 0.05)}
side = {round(d, 2): outline_cost(0, d, 0) for d in (-0.15, -0.10, -0.05, 0.05, 0.10, 0.15)}
r = np.hypot(*np.meshgrid((np.arange(W) + 0.5) * res + ox - x0, (np.arange(H) + 0.5) * res + oy - y0))
near = r < 0.35
left = max((d for d in range(0, 91, 5) if all(ok(turns[k]) for k in range(0, d + 1, 5))), default=0)
right = max((d for d in range(0, 91, 5) if all(ok(turns[-k]) for k in range(0, d + 1, 5))), default=0)
fwd = max((d for d in ahead if all(ok(ahead[k]) for k in ahead if k <= d)), default=0.0)
print(f'track_unknown_space {sys.argv[1]:5s}: cells within 0.35 m: unknown {int((g[near] == 255).sum())}, '
      f'lethal {int((g[near] == 254).sum())}, of {int(near.sum())} | in-place turn allowed up to: left {left} deg, right {right} deg '
      f'| straight ahead allowed {fwd:.2f} m | sideways ok: {[d for d in side if ok(side[d])]} '
      f'| blocked turn angles by unknown: {sum(1 for c in turns.values() if c == 255)}/37, by lethal: {sum(1 for c in turns.values() if c == 254)}/37')
PY
  kill $PID 2>/dev/null; wait $PID 2>/dev/null; PID=
  sleep 2
done
