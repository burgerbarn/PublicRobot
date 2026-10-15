import math, time, numpy as np, cv2, rclpy
from nav_msgs.msg import OccupancyGrid
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import QoSProfile, DurabilityPolicy
rclpy.init(); n = rclpy.create_node('scan_overlay'); g = {}
n.create_subscription(OccupancyGrid, '/map', lambda m: g.__setitem__('m', m), QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
n.create_subscription(LaserScan, '/scan', lambda m: g.__setitem__('s', m), 5)
n.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', lambda m: g.__setitem__('p', m), QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
t = time.time()
while time.time() - t < 8 and not all(k in g for k in 'msp'): rclpy.spin_once(n, timeout_sec=0.1)
m, s, p = g['m'], g['s'], g['p'].pose.pose
M = np.array(m.data, np.int16).reshape(m.info.height, m.info.width); r = m.info.resolution; ox, oy = m.info.origin.position.x, m.info.origin.position.y
yaw = 2 * math.atan2(p.orientation.z, p.orientation.w); x0, y0 = p.position.x, p.position.y
ang = s.angle_min + np.arange(len(s.ranges)) * s.angle_increment + math.pi; rr = np.array(s.ranges); ok = np.isfinite(rr) & (rr > 0.12)
lx = 0.048 + rr[ok] * np.cos(ang[ok]); ly = rr[ok] * np.sin(ang[ok])
wx = x0 + lx * math.cos(yaw) - ly * math.sin(yaw); wy = y0 + lx * math.sin(yaw) + ly * math.cos(yaw)
i = ((wx - ox) / r).astype(int); j = ((wy - oy) / r).astype(int)
occ = (M >= 65).astype(np.uint8); from scipy import ndimage; dist = ndimage.distance_transform_edt(1 - occ) * r
inb = (i >= 0) & (i < M.shape[1]) & (j >= 0) & (j < M.shape[0])
d = dist[j[inb], i[inb]]
print(f'amcl pose ({x0:.2f},{y0:.2f}) yaw {math.degrees(yaw):.0f}; scan points on map {inb.sum()}/{len(wx)}; within 10 cm of a map wall {100*(d<0.10).mean():.0f}%, median dist {np.median(d):.2f} m')
img = np.full((*M.shape, 3), 200, np.uint8); img[M == 0] = (255, 255, 255); img[M >= 65] = (0, 0, 0)
for a, b in zip(i[inb], j[inb]): img[b, a] = (0, 0, 255)
ci, cj = int((x0 - ox) / r), int((y0 - oy) / r); cv2.circle(img, (ci, cj), 2, (0, 170, 0), -1)
cv2.imwrite('/tmp/overlay.png', cv2.resize(np.flipud(img), (M.shape[1] * 6, M.shape[0] * 6), interpolation=cv2.INTER_NEAREST))
