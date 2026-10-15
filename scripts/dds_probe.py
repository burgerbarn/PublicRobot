"""Time for a fresh node to receive the first message on key topics (DDS discovery health)."""
import sys, time, rclpy
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
rclpy.init(); n = rclpy.create_node("dds_probe"); t0 = time.time(); first = {}
for name, typ in (("rosorin_board_driver/state", String), ("scan", LaserScan), ("odometry/filtered", Odometry)):
    n.create_subscription(typ, name, lambda m, k=name: first.setdefault(k, time.time() - t0), 10)
while len(first) < 3 and time.time() - t0 < float(sys.argv[1] if len(sys.argv) > 1 else 20):
    rclpy.spin_once(n, timeout_sec=0.05)
print(" ".join(f"{k.split('/')[-1]}={first[k]:.1f}s" if k in first else f"{k.split('/')[-1]}=NONE"
               for k in ("rosorin_board_driver/state", "scan", "odometry/filtered")), flush=True)
