"""Publish /initialpose (map frame) and report AMCL's estimate. Usage: set_initial_pose.py x y yaw_deg"""
import math, sys, time, rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
x, y, yaw = float(sys.argv[1]), float(sys.argv[2]), math.radians(float(sys.argv[3]))
rclpy.init(); n = rclpy.create_node('set_initial_pose'); got = {}
pub = n.create_publisher(PoseWithCovarianceStamped, '/initialpose', 1)
n.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', lambda m: got.__setitem__('p', m), 10)
m = PoseWithCovarianceStamped(); m.header.frame_id = 'map'
m.pose.pose.position.x, m.pose.pose.position.y = x, y
qz, qw = math.sin(yaw / 2), math.cos(yaw / 2); nrm = math.hypot(qz, qw)
m.pose.pose.orientation.z, m.pose.pose.orientation.w = qz / nrm, qw / nrm
cov = [0.0] * 36; cov[0] = cov[7] = 0.04; cov[35] = math.radians(8) ** 2; m.pose.covariance = cov
time.sleep(1.0)
for _ in range(3):
    m.header.stamp = n.get_clock().now().to_msg(); pub.publish(m); rclpy.spin_once(n, timeout_sec=0.5)
t = time.time()
while time.time() - t < 8: rclpy.spin_once(n, timeout_sec=0.1)
p = got.get('p')
if p is None: sys.exit('no /amcl_pose')
q = p.pose.pose.orientation; c = p.pose.covariance
print(f'amcl: x {p.pose.pose.position.x:.3f} y {p.pose.pose.position.y:.3f} yaw {math.degrees(2 * math.atan2(q.z, q.w)):.1f} deg | std x {math.sqrt(c[0]):.3f} y {math.sqrt(c[7]):.3f} m, yaw {math.degrees(math.sqrt(c[35])):.1f} deg')
