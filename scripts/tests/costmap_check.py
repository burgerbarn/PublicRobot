#!/usr/bin/env python3
"""Does the local costmap show what the sensors see? Robot standing still, navigation up.
Lethal costmap cells are compared with (a) LiDAR hits and (b) depth points 0.03-0.6 m above base_link height,
both placed in the costmap frame via TF. Writes /tmp/costmap_check.png (grey = free, white = lethal explained by a
sensor, red = lethal with no sensor point within 10 cm, blue = depth obstacle the costmap does NOT mark,
green = LiDAR hit not marked, yellow dot = robot)."""
import math, sys, time

import cv2
import numpy as np
import rclpy
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py import point_cloud2 as pc2
from tf2_ros import Buffer, TransformListener

TOPIC = sys.argv[1] if len(sys.argv) > 1 else '/local_costmap/costmap'


def T_of(buf, a, b):
    t = buf.lookup_transform(a, b, Time()).transform
    q, p = t.rotation, t.translation
    x, y, z, w = q.x, q.y, q.z, q.w
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    return R, np.array([p.x, p.y, p.z])


def main():
    rclpy.init()
    n = rclpy.create_node('costmap_check')
    b = {}
    buf = Buffer(); TransformListener(buf, n)
    n.create_subscription(OccupancyGrid, TOPIC, lambda m: b.__setitem__('c', m), 1)
    n.create_subscription(LaserScan, '/scan', lambda m: b.__setitem__('s', m), qos_profile_sensor_data)
    n.create_subscription(PointCloud2, '/aurora/points2', lambda m: b.__setitem__('p', m), qos_profile_sensor_data)
    t0 = time.time()
    while time.time() - t0 < 12 and (len(b) < 3 or time.time() - t0 < 5):
        rclpy.spin_once(n, timeout_sec=0.2)
    if len(b) < 3:
        print('missing', {'c', 's', 'p'} - set(b)); return
    c = b['c']; res, w, h = c.info.resolution, c.info.width, c.info.height
    ox, oy = c.info.origin.position.x, c.info.origin.position.y
    g = np.array(c.data, np.int16).reshape(h, w)
    frame = c.header.frame_id
    Rb, pb = T_of(buf, frame, 'base_link')
    # LiDAR hits in the costmap frame
    s = b['s']; r = np.asarray(s.ranges, np.float32)
    ang = s.angle_min + np.arange(len(r)) * s.angle_increment
    ok = np.isfinite(r) & (r > 0.12) & (r < 2.5)
    Rl, pl = T_of(buf, frame, s.header.frame_id)
    L = (np.c_[r[ok] * np.cos(ang[ok]), r[ok] * np.sin(ang[ok]), np.zeros(ok.sum())] @ Rl.T) + pl
    # depth points 0.03-0.6 m above base_link height, in the costmap frame
    pts = np.array([[q[0], q[1], q[2]] for q in pc2.read_points(b['p'], field_names=('x', 'y', 'z'), skip_nans=True)])
    pts = pts[pts[:, 2] > 0][::3]
    Rc, pc = T_of(buf, frame, b['p'].header.frame_id)
    P = pts @ Rc.T + pc
    zb = P[:, 2] - pb[2]
    D = P[(zb > 0.03) & (zb < 0.6)]
    F = P[(zb <= 0.03) & (zb > -0.1)]

    def cells(X):
        i = np.floor((X[:, 0] - ox) / res).astype(int); j = np.floor((X[:, 1] - oy) / res).astype(int)
        m = np.zeros((h, w), bool); k = (i >= 0) & (i < w) & (j >= 0) & (j < h); m[j[k], i[k]] = True
        return m
    mL, mD, mF = cells(L), cells(D), cells(F)
    # a depth cell counts when it holds >= 3 points (single points = noise)
    i = np.floor((D[:, 0] - ox) / res).astype(int); j = np.floor((D[:, 1] - oy) / res).astype(int)
    k = (i >= 0) & (i < w) & (j >= 0) & (j < h); cnt = np.zeros((h, w), int); np.add.at(cnt, (j[k], i[k]), 1)
    mD = cnt >= 3
    lethal = g >= 100
    near = cv2.dilate((mL | mD).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0       # within 2 cells = 10 cm
    leth_near = cv2.dilate(lethal.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    print(f'{TOPIC} frame {frame} {w}x{h} @ {res} m; cells: lethal {int(lethal.sum())}, LiDAR {int(mL.sum())}, '
          f'depth-obstacle {int(mD.sum())}, floor seen by the camera {int(mF.sum())}')
    print(f'lethal cells with a sensor point within 10 cm: {int((lethal & near).sum())}/{int(lethal.sum())}')
    print(f'depth-obstacle cells marked lethal (within 10 cm): {int((mD & leth_near).sum())}/{int(mD.sum())}')
    print(f'LiDAR cells marked lethal (within 10 cm): {int((mL & leth_near).sum())}/{int(mL.sum())}')
    fl = mF & ~cv2.dilate(mD.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~near
    print(f'floor cells (camera sees floor, no obstacle near) wrongly lethal: {int((fl & lethal).sum())}/{int(fl.sum())}')
    print(f'depth points: {len(P)} total, floor height median {np.median(zb[(zb < 0.03) & (zb > -0.1)]):+.3f} m (vs base_link)')
    img = np.full((h, w, 3), 90, np.uint8)
    img[g < 0] = (60, 60, 60); img[(g > 0) & (g < 100)] = (140, 140, 140)
    img[lethal & near] = (255, 255, 255); img[lethal & ~near] = (0, 0, 255)
    img[mD & ~leth_near] = (255, 0, 0); img[mL & ~leth_near] = (0, 255, 0)
    rx, ry = int((pb[0] - ox) / res), int((pb[1] - oy) / res)
    cv2.circle(img, (rx, ry), 2, (0, 255, 255), -1)
    hx, hy = Rb[0, 0], Rb[1, 0]
    cv2.line(img, (rx, ry), (int(rx + 8 * hx), int(ry + 8 * hy)), (0, 255, 255), 1)
    cv2.imwrite('/tmp/costmap_check.png', cv2.resize(cv2.flip(img, 0), None, fx=6, fy=6, interpolation=cv2.INTER_NEAREST))


if __name__ == '__main__':
    main()
