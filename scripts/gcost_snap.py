"""Render /global_costmap/costmap (+ static map) with the robot (amcl) and a goal marked. Usage: gcost_snap.py gx gy"""
import math, sys, time, numpy as np, cv2, rclpy
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import QoSProfile, DurabilityPolicy
gx, gy = float(sys.argv[1]), float(sys.argv[2])
rclpy.init(); n = rclpy.create_node('gcost_snap'); g = {}
q = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
n.create_subscription(OccupancyGrid, '/global_costmap/costmap', lambda m: g.__setitem__('c', m), q)
n.create_subscription(OccupancyGrid, '/map', lambda m: g.__setitem__('m', m), q)
n.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', lambda m: g.__setitem__('p', m), 10)
t = time.time()
while time.time() - t < 6 and not all(k in g for k in 'cm'): rclpy.spin_once(n, timeout_sec=0.1)
def grid(m): return np.array(m.data, np.int16).reshape(m.info.height, m.info.width), m.info
C, ci = grid(g['c']); M, mi = grid(g['m'])
def cell(info, x, y): return int((x - info.origin.position.x) / info.resolution), int((y - info.origin.position.y) / info.resolution)
p = g['p'].pose.pose if 'p' in g else None
rxy = (p.position.x, p.position.y) if p else (1.12, 3.33)
for name, (x, y) in (('robot', rxy), ('goal', (gx, gy))):
    i, j = cell(ci, x, y); print(f'{name} ({x:.2f},{y:.2f}): costmap {C[j, i] if 0 <= j < C.shape[0] and 0 <= i < C.shape[1] else "out"} | static map {M[cell(mi, x, y)[1], cell(mi, x, y)[0]]}')
img = np.full((*C.shape, 3), 60, np.uint8); img[C == 0] = (235, 235, 235); img[(C > 0) & (C < 99)] = (120, 170, 255); img[C >= 99] = (0, 0, 200)
img[M.shape[0] - 1 - np.arange(0)] if False else None
for (x, y), col in ((rxy, (0, 180, 0)), ((gx, gy), (255, 0, 255))):
    i, j = cell(ci, x, y); cv2.circle(img, (i, j), 2, col, -1)
img = cv2.resize(np.flipud(img), (C.shape[1] * 6, C.shape[0] * 6), interpolation=cv2.INTER_NEAREST)
cv2.imwrite('/tmp/gcost.png', img); print('lethal cells', int((C >= 99).sum()), 'of', C.size)
