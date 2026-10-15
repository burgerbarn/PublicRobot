"""Records nvblox's obstacle slice (what the Nav2 nvblox layer receives) with the robot and camera pose at each
slice, during a bag replay on the isolated domain. Output: one .npz. Used by nvblox_decay_replay.sh."""
import math, signal, sys

import numpy as np
import rclpy
from nvblox_msgs.msg import DistanceMapSlice
from rclpy.parameter import Parameter
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo
from tf2_ros import Buffer, TransformListener

out = sys.argv[1]
rclpy.init()
n = rclpy.create_node('slice_recorder', parameter_overrides=[Parameter('use_sim_time', value=True)])
buf = Buffer(); TransformListener(buf, n)
cam = {'frame': None}
n.create_subscription(CameraInfo, '/aurora/rgb/camera_info', lambda m: cam.__setitem__('frame', m.header.frame_id), 5)
rows, grids, last = [], [], [0.0]


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def on_slice(m):
    t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
    if t - last[0] < 1.0:                                   # one snapshot per second of recorded time
        return
    last[0] = t
    base, cdir = (np.nan,) * 3, (np.nan,) * 3
    try:
        b = buf.lookup_transform(m.header.frame_id, 'base_link', Time()).transform
        base = (b.translation.x, b.translation.y, yaw_of(b.rotation))
        if cam['frame']:
            c = buf.lookup_transform(m.header.frame_id, cam['frame'], Time()).transform
            q = c.rotation                                   # optical z axis (viewing direction) in the map frame
            zx = 2 * (q.x * q.z + q.w * q.y); zy = 2 * (q.y * q.z - q.w * q.x)
            cdir = (c.translation.x, c.translation.y, math.atan2(zy, zx))
    except Exception:
        pass
    d = np.array(m.data, np.float32).reshape(m.height, m.width)
    rows.append((t, m.origin.x, m.origin.y, m.resolution, m.width, m.height, m.unknown_value, *base, *cdir[:3]))
    grids.append(d)


n.create_subscription(DistanceMapSlice, '/nvblox_node/static_map_slice', on_slice, 5)
stop = [False]
signal.signal(signal.SIGINT, lambda *a: stop.__setitem__(0, True))
while rclpy.ok() and not stop[0]:
    rclpy.spin_once(n, timeout_sec=0.2)
g = np.empty(len(grids), object)                             # slices change size as the map grows
for i, a in enumerate(grids):
    g[i] = a
np.savez_compressed(out, rows=np.array(rows, np.float64), grids=g)
print(f'{len(rows)} slices saved to {out}')
