"""Collect static accelerometer poses from imu/data_raw (for 6-position calibration)."""
import json, math, statistics, sys, time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu

G = 9.80665
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 240
rclpy.init(); n = Node("accel_collect")
buf = []; poses = []
def cb(m):
    a = (m.linear_acceleration.x / G, m.linear_acceleration.y / G, m.linear_acceleration.z / G)
    w = math.sqrt(m.angular_velocity.x**2 + m.angular_velocity.y**2 + m.angular_velocity.z**2)
    buf.append((time.monotonic(), a, w))
n.create_subscription(Imu, "imu/data_raw", cb, 100)
t0 = time.monotonic(); print("collecting", DUR, "s", flush=True)
while time.monotonic() - t0 < DUR:
    rclpy.spin_once(n, timeout_sec=0.05)
    now = time.monotonic()
    buf[:] = [b for b in buf if now - b[0] <= 2.0]
    if len(buf) < 150:
        continue
    win = buf
    still = max(b[2] for b in win) < 0.06 and all(statistics.pstdev(b[1][i] for b in win) < 0.01 for i in range(3))
    if not still:
        continue
    mean = tuple(statistics.mean(b[1][i] for b in win) for i in range(3))
    if poses and min(math.dist(mean, p) for p in poses) < 0.25:
        continue                                       # same pose as one already recorded
    poses.append(mean)
    print("t=%5.1fs pose %d: a=(%+.4f, %+.4f, %+.4f) g  |a|=%.4f" % (now - t0, len(poses), *mean, math.sqrt(sum(x*x for x in mean))), flush=True)
json.dump(poses, open("/tmp/accel_poses.json", "w"))
print("done:", len(poses), "poses saved to /tmp/accel_poses.json", flush=True)
