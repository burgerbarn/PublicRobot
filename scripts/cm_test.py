"""Collision monitor static test (motion must be DISABLED): report scan points in the stop/slow zones,
feed cmd_vel_smoothed, print what collision_monitor outputs on cmd_vel."""
import math, time
import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
STOP = (-0.10, 0.335, -0.215, 0.22); SLOW = (-0.10, 0.605, -0.315, 0.32)
rclpy.init(); n = rclpy.create_node('cm_test'); out = []; sc = []
n.create_subscription(Twist, 'cmd_vel', lambda m: out.append((m.linear.x, m.linear.y, m.angular.z)), 10)
n.create_subscription(LaserScan, 'scan', sc.append, 5)
pub = n.create_publisher(Twist, 'cmd_vel_smoothed', 10)
t = time.time()
while time.time() - t < 2 or not sc: rclpy.spin_once(n, timeout_sec=0.05)
s = sc[-1]; P = []
for i, r in enumerate(s.ranges):
    if s.range_min <= r <= s.range_max:
        a = s.angle_min + i * s.angle_increment + math.pi; P.append((0.048 + r * math.cos(a), r * math.sin(a)))
P = np.array(P)
for name, (x0, x1, y0, y1) in (('stop', STOP), ('slow', SLOW)):
    k = (P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1)
    print(f'{name} zone: {int(k.sum())} points' + (f', nearest x {P[k][:, 0].min():.3f} m' if k.any() else ''))
for vx, vy in ((0.1, 0.0), (0.0, 0.1), (0.0, -0.1)):
    out.clear(); m = Twist(); m.linear.x, m.linear.y = vx, vy; t = time.time()
    while time.time() - t < 1.5: pub.publish(m); rclpy.spin_once(n, timeout_sec=0.05)
    print(f'in vx {vx:.2f} vy {vy:.2f} -> out ' + (str([tuple(round(c, 3) for c in o) for o in out[-2:]]) if out else 'NOTHING published'))
