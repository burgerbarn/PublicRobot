"""Part of amcl_replay.sh: passes the bag's TF on without its map->odom, and scores every pose AMCL publishes against
the newest scan (scan fit, beams through walls). Prints one summary line on Ctrl-C."""
import json, math, sys

import numpy as np
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.executors import ExternalShutdownException
from rclpy.parameter import Parameter
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import LaserScan
from tf2_msgs.msg import TFMessage

from rosorin_base.nav_supervisor import ScanMatcher, yaw_of

LATCHED = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
rclpy.init(); n = rclpy.create_node('amcl_replay_judge', parameter_overrides=[Parameter('use_sim_time', value=True)])
g = {'rows': []}
tf_pub = n.create_publisher(TFMessage, '/tf', 100)
def on_tf(m):
    keep = [t for t in m.transforms if not (t.header.frame_id == 'map' and t.child_frame_id == 'odom')]
    if keep:
        tf_pub.publish(TFMessage(transforms=keep))
def on_pose(m):
    s, M = g.get('scan'), g.get('M')
    if s is None or M is None:
        return
    p = m.pose.pose; pose = np.array([p.position.x, p.position.y, yaw_of(p.orientation)])
    r = np.asarray(s.ranges, np.float32); ok = np.isfinite(r) & (r > 0.12) & (r < s.range_max)
    a = s.angle_min + np.arange(len(r)) * s.angle_increment + math.pi          # laser: 0.048 m ahead, yaw pi (URDF)
    pts = np.stack([0.048 + r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])], 1)
    cov = m.pose.covariance
    g['rows'].append([float(M.fits(pose[None], pts)[0]), float(M.through_walls(pose[None], pts)[0]),
                      float(M.refine(pose, pts)[1]), math.sqrt(max(cov[0] + cov[7], 0.0)), *pose.tolist()])
n.create_subscription(TFMessage, '/tf_bag', on_tf, 100)
n.create_subscription(OccupancyGrid, '/map', lambda m: g.__setitem__('M', ScanMatcher(m)), LATCHED)
n.create_subscription(LaserScan, '/scan', lambda m: g.__setitem__('scan', m), 10)
n.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', on_pose, LATCHED)
try:
    rclpy.spin(n)
except (KeyboardInterrupt, ExternalShutdownException):
    pass
R = np.array(g['rows'])
json.dump(g['rows'], open(sys.argv[1], 'w'))
if len(R):
    print(f'{len(R)} poses | fit median {np.median(R[:, 0]):.2f} min {R[:, 0].min():.2f} | after refine {np.median(R[:, 2]):.2f} | '
          f'through walls median {np.median(R[:, 1]):.2f} max {R[:, 1].max():.2f} | pose std {np.median(R[:, 3]):.2f} m | '
          f'end ({R[-1, 4]:.2f},{R[-1, 5]:.2f},{math.degrees(R[-1, 6]):.0f})')
else:
    print('no poses from AMCL')
