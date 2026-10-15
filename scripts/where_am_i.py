"""Brute-force scan matching of the live LiDAR scan against a saved occupancy map (what the GPU localizer does, but with no
minimum scan field of view and with the scores shown). Read-only: no ROS publishing, no motion.
Usage: where_am_i.py [map.yaml] [seed_x seed_y seed_radius_m]   -> prints the best poses and the fit of the best one.
Run with the vision venv (numpy, scipy, cv2, PIL) after sourcing ROS."""
import math, sys, time

import numpy as np
import rclpy
import yaml
from PIL import Image
from scipy import ndimage
from sensor_msgs.msg import LaserScan

mp = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].replace('.', '').replace('-', '').isdigit() else '/home/burgerbarn/maps/room_live.yaml'
cfg = yaml.safe_load(open(mp)); res = cfg['resolution']; ox, oy = cfg['origin'][:2]
img = np.array(Image.open(mp.rsplit('/', 1)[0] + '/' + cfg['image']).convert('L'), np.float32)
occ = (255 - img) / 255.0 >= cfg['occupied_thresh']; free = (255 - img) / 255.0 <= cfg['free_thresh']
H = img.shape[0]; occ = np.flipud(occ); free = np.flipud(free)           # row 0 = bottom (map frame y up)
dist = ndimage.distance_transform_edt(~occ) * res
rclpy.init(); n = rclpy.create_node('where_am_i'); got = []
n.create_subscription(LaserScan, '/scan', got.append, 5)
t = time.time()
while len(got) < 5 and time.time() - t < 10:
    rclpy.spin_once(n, timeout_sec=0.2)
s = got[-1]
r = np.array(s.ranges); ok = np.isfinite(r) & (r > s.range_min) & (r < s.range_max)
ang = s.angle_min + np.arange(len(r)) * s.angle_increment + math.pi          # laser mounted yaw pi
lx = 0.048 + r[ok] * np.cos(ang[ok]); ly = r[ok] * np.sin(ang[ok])
print(f'{ok.sum()} valid scan points, map {img.shape[1] * res:.1f} x {H * res:.1f} m')
seed = [float(a) for a in sys.argv[2:5]] if len(sys.argv) >= 5 else None
best = []
ys, xs = np.nonzero(free)
cx, cy = ox + (xs + 0.5) * res, oy + (ys + 0.5) * res
if seed:
    k = np.hypot(cx - seed[0], cy - seed[1]) <= seed[2]; cx, cy = cx[k], cy[k]
for yaw in np.radians(np.arange(0, 360, 3)):
    c, sn = math.cos(yaw), math.sin(yaw)
    wx = cx[:, None] + lx[None, :] * c - ly[None, :] * sn; wy = cy[:, None] + lx[None, :] * sn + ly[None, :] * c
    i = ((wx - ox) / res).astype(int); j = ((wy - oy) / res).astype(int)
    inb = (i >= 0) & (i < dist.shape[1]) & (j >= 0) & (j < dist.shape[0])
    d = np.where(inb, dist[np.clip(j, 0, dist.shape[0] - 1), np.clip(i, 0, dist.shape[1] - 1)], 9.9)
    fit = (d < 0.10).mean(axis=1)
    b = fit.argmax(); best.append((fit[b], cx[b], cy[b], math.degrees(yaw), float(np.median(d[b]))))
best.sort(reverse=True)
print('best poses (fit = share of scan points within 10 cm of a map wall):')
for f, x, y, yw, md in best[:5]:
    print(f'  fit {f * 100:4.0f} %  x {x:6.2f}  y {y:6.2f}  yaw {yw:5.0f} deg  median distance {md:.2f} m')
f, x, y, yw, md = best[0]
second = next((b for b in best[1:] if math.hypot(b[1] - x, b[2] - y) > 0.5 or abs(((b[3] - yw + 180) % 360) - 180) > 30), None)
print('best/second-best (different place) fit: %.0f %% / %s' % (f * 100, '%.0f %%' % (second[0] * 100) if second else '-'))
print(f'RESULT {x:.3f} {y:.3f} {yw:.1f} {f:.3f}')
