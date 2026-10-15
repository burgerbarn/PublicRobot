"""Log imu/data_raw (accel in g, gyro rad/s) to CSV for offline pose extraction."""
import sys, time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Imu
G = 9.80665; DUR = float(sys.argv[1]); out = open(sys.argv[2], "w")
rclpy.init(); n = Node("imu_raw_log"); t0 = time.monotonic()
def cb(m):
    out.write("%.3f,%.5f,%.5f,%.5f,%.5f,%.5f,%.5f\n" % (time.monotonic() - t0, m.linear_acceleration.x / G, m.linear_acceleration.y / G,
              m.linear_acceleration.z / G, m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z))
n.create_subscription(Imu, "imu/data_raw", cb, 200)
while time.monotonic() - t0 < DUR:
    rclpy.spin_once(n, timeout_sec=0.05)
out.close(); print("logged", DUR, "s", flush=True)
