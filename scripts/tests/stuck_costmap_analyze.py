"""Why was the robot stuck? From a replayed run bag (isolated domain): at each second, take the LOCAL costmap and the robot
pose (odom -> base_link from /tf) and test the footprint outline like DWB's ObstacleFootprint critic: highest cost on the
outline at the robot's own pose and turned in place, plus the cells around the robot (lethal / inscribed / inflated).
Usage: (replay the bag with --topics /tf /tf_static /local_costmap/costmap) python3 stuck_costmap_analyze.py [from_s] [to_s]"""
import math, sys, time

import numpy as np
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener

FOOT = [(0.165, 0.13), (0.165, -0.125), (-0.16, -0.125), (-0.16, 0.13)]
rclpy.init(); n = rclpy.create_node('stuck_analyze'); buf = Buffer(); TransformListener(buf, n)
rows, t0 = [], [None]


def outline(g, ox, oy, res, px, py, yaw):
    pts = [(px + math.cos(yaw) * fx - math.sin(yaw) * fy, py + math.sin(yaw) * fx + math.cos(yaw) * fy) for fx, fy in FOOT]
    worst = 0
    for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
        for k in np.linspace(0, 1, int(math.hypot(bx - ax, by - ay) / (res / 2)) + 2):
            i, j = int((ax + (bx - ax) * k - ox) / res), int((ay + (by - ay) * k - oy) / res)
            if not (0 <= i < g.shape[1] and 0 <= j < g.shape[0]):
                return 254
            worst = max(worst, int(g[j, i]))
    return worst


def on_cost(m):
    t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
    t0[0] = t0[0] or t
    try:
        tr = buf.lookup_transform('odom', 'base_link', Time()).transform
    except Exception:
        return
    q = tr.rotation; yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
    g = np.array(m.data, np.int16).reshape(m.info.height, m.info.width); g = np.where(g < 0, 255, g).astype(np.int16)
    ox, oy, res = m.info.origin.position.x, m.info.origin.position.y, m.info.resolution
    here = outline(g, ox, oy, res, tr.translation.x, tr.translation.y, yaw)
    turns = [outline(g, ox, oy, res, tr.translation.x, tr.translation.y, yaw + math.radians(d)) for d in range(-90, 91, 15)]
    yy, xx = np.mgrid[0:g.shape[0], 0:g.shape[1]]
    d = np.hypot(xx * res + ox + res / 2 - tr.translation.x, yy * res + oy + res / 2 - tr.translation.y)
    near = d < 0.30
    rows.append((t - t0[0], tr.translation.x, tr.translation.y, here, sum(1 for c in turns if c >= 253), int((g[near] >= 253).sum()),
                 int(((g[near] > 0) & (g[near] < 253)).sum()), int((g[near] == 255).sum())))


n.create_subscription(OccupancyGrid, '/local_costmap/costmap', on_cost, 5)
a, b = (float(sys.argv[1]) if len(sys.argv) > 1 else 0), (float(sys.argv[2]) if len(sys.argv) > 2 else 1e9)
last, end = time.time(), time.time() + 150
while time.time() < end:
    rclpy.spin_once(n, timeout_sec=0.3)
    if rows and time.time() - last > 8 and len(rows) == getattr(on_cost, 'n', 0):
        break
    if len(rows) != getattr(on_cost, 'n', 0):
        on_cost.n, last = len(rows), time.time()
print('t(s)  odom x,y        outline cost at pose | turn angles blocked (of 13) | cells <0.3 m: lethal/inscribed, inflated, unknown')
seen = -99
for r in rows:
    if a <= r[0] <= b and r[0] - seen >= 5:
        seen = r[0]
        print(f'{r[0]:5.0f} ({r[1]:5.2f},{r[2]:5.2f})   {r[3]:3d}                  | {r[4]:2d}                         | {r[5]:3d}, {r[6]:3d}, {r[7]:3d}')
