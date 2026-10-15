"""Part of slam_continue_replay.sh: passes the bag's TF on without its map->odom and watches slam_toolbox continue a
saved map: for every scan, how well it fits the map slam_toolbox publishes at the pose slam_toolbox gives; at the end,
how the map changed against the map it started from (walls kept, new walls standing in old free space = doubled
walls, the corruption of 2026-10-02). Prints a summary on Ctrl-C and saves the first and last map as PNG."""
import json, math, sys

import cv2
import numpy as np
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.executors import ExternalShutdownException
from rclpy.parameter import Parameter
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage
from tf2_ros import Buffer, TransformListener

from rosorin_base.nav_supervisor import ScanMatcher, yaw_of

OUT = sys.argv[1]
LATCHED = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
rclpy.init(); n = rclpy.create_node('slam_continue_judge', parameter_overrides=[Parameter('use_sim_time', value=True)])
g = {'rows': [], 'maps': 0}
buf = Buffer(); TransformListener(buf, n)
tf_pub = n.create_publisher(TFMessage, '/tf', 100)
def on_tf(m):
    keep = [t for t in m.transforms if not (t.header.frame_id == 'map' and t.child_frame_id == 'odom')]
    if keep:
        tf_pub.publish(TFMessage(transforms=keep))
def on_map(m):
    g['maps'] += 1; g.setdefault('first', m); g['last'] = m; g['M'] = ScanMatcher(m)
def on_scan(s):
    M = g.get('M')
    if M is None:
        return
    try:
        t = buf.lookup_transform('map', 'base_link', Time()).transform
    except Exception:
        return
    pose = np.array([t.translation.x, t.translation.y, yaw_of(t.rotation)])
    r = np.asarray(s.ranges, np.float32); ok = np.isfinite(r) & (r > 0.12) & (r < s.range_max)
    a = s.angle_min + np.arange(len(r)) * s.angle_increment + math.pi
    pts = np.stack([0.048 + r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])], 1)
    if len(pts) >= 60:
        g['rows'].append([float(M.fits(pose[None], pts)[0]), *pose.tolist(), s.header.stamp.sec + s.header.stamp.nanosec * 1e-9])
n.create_subscription(TFMessage, '/tf_bag', on_tf, 100)
n.create_subscription(OccupancyGrid, '/map', on_map, LATCHED)
n.create_subscription(LaserScan, '/scan', on_scan, 10)
try:
    rclpy.spin(n)
except (KeyboardInterrupt, ExternalShutdownException):
    pass
R = np.array(g['rows'])
def grid(m):
    a = np.array(m.data, np.int16).reshape(m.info.height, m.info.width)
    return a, m.info.origin.position.x, m.info.origin.position.y, m.info.resolution
def png(m, path):
    a = grid(m)[0]; img = np.full(a.shape, 128, np.uint8); img[a == 0] = 255; img[a >= 65] = 0
    cv2.imwrite(path, cv2.resize(np.flipud(img), None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))
if 'first' in g and len(R):
    png(g['first'], OUT + '_first.png'); png(g['last'], OUT + '_last.png')
    a0, x0, y0, res = grid(g['first']); a1, x1, y1, _ = grid(g['last'])
    ys, xs = np.nonzero(a1 >= 65)                                  # walls of the last map, looked up in the first
    i = np.round((x1 + (xs + 0.5) * res - x0) / res - 0.5).astype(int); j = np.round((y1 + (ys + 0.5) * res - y0) / res - 0.5).astype(int)
    inb = (i >= 0) & (i < a0.shape[1]) & (j >= 0) & (j < a0.shape[0])
    d0 = cv2.distanceTransform((a0 < 65).astype(np.uint8), cv2.DIST_L2, 5) * res
    ghost = float(((a0[j[inb], i[inb]] == 0) & (d0[j[inb], i[inb]] > 0.15)).mean()) if inb.any() else 0.0
    ys0, xs0 = np.nonzero(a0 >= 65)
    i0 = np.round((x0 + (xs0 + 0.5) * res - x1) / res - 0.5).astype(int); j0 = np.round((y0 + (ys0 + 0.5) * res - y1) / res - 0.5).astype(int)
    inb0 = (i0 >= 0) & (i0 < a1.shape[1]) & (j0 >= 0) & (j0 < a1.shape[0])
    d1 = cv2.distanceTransform((a1 < 65).astype(np.uint8), cv2.DIST_L2, 5) * res
    kept = float((d1[j0[inb0], i0[inb0]] <= 0.10).mean()) if inb0.any() else 0.0
    h = len(R) // 2
    print(f'{len(R)} scans, {g["maps"]} map updates | fit to the live map: first half median {np.median(R[:h, 0]):.2f}, second half {np.median(R[h:, 0]):.2f}, '
          f'min {R[:, 0].min():.2f} | old walls still walls {kept:.2f} | new walls standing in old free space {ghost:.2f} | '
          f'map {a0.shape[1]}x{a0.shape[0]} -> {a1.shape[1]}x{a1.shape[0]} | end ({R[-1, 1]:.2f},{R[-1, 2]:.2f},{math.degrees(R[-1, 3]):.0f})')
    json.dump(g['rows'], open(OUT + '_rows.json', 'w'))
else:
    print(f'no result: maps {g["maps"]}, scans scored {len(R)}')
