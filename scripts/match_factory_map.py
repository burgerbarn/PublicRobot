"""Global scan-to-map match of the current scan against a factory map (PGM + origin/res), as-is and
mirrored (y flipped). Brute force over all free cells x 2-deg headings, likelihood field score."""
import math, sys, time
import numpy as np
from scipy import ndimage
import rclpy
from sensor_msgs.msg import LaserScan

PGM, RES, ORIGIN = sys.argv[1], float(sys.argv[2]), (float(sys.argv[3]), float(sys.argv[4]))
LASER_X, LASER_YAW = 0.048156, math.pi

def read_pgm(p):
    b = open(p, 'rb').read(); parts = b.split(b'\n', 3); w, h = map(int, parts[1].split())
    return np.frombuffer(parts[3][:w * h], np.uint8).reshape(h, w)

rclpy.init(); n = rclpy.create_node('match'); scans = []
n.create_subscription(LaserScan, 'scan', scans.append, 10)
t = time.time()
while len(scans) < 15 and time.time() - t < 15: rclpy.spin_once(n, timeout_sec=0.05)
m = scans[-1]; R = np.array([s.ranges for s in scans], float)
R[(R < m.range_min) | (R > m.range_max) | ~np.isfinite(R)] = np.nan
r = np.nanmedian(R, 0); a = m.angle_min + np.arange(len(r)) * m.angle_increment + LASER_YAW; ok = np.isfinite(r)
P = np.c_[LASER_X + r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])]
print('scan points', len(P))

img = read_pgm(PGM)[::-1]                      # row 0 = min y
occ, free = img < 50, img > 250
for tag, flip in (('as-is', False), ('mirrored', True)):
    O, F = (occ[::-1], free[::-1]) if flip else (occ, free)   # mirror the map in y
    dist = ndimage.distance_transform_edt(~O) * RES
    like = np.exp(-(dist / 0.1) ** 2)
    fy, fx = np.nonzero(F); sel = np.arange(0, len(fx), 2)       # every other free cell (0.05 m grid)
    cx, cy = fx[sel] * RES, fy[sel] * RES
    best = []
    for th in np.radians(np.arange(0, 360, 2)):
        c, s = math.cos(th), math.sin(th)
        Q = P @ np.array([[c, s], [-s, c]])                          # rotated scan
        gx = ((cx[:, None] + Q[None, :, 0]) / RES).astype(int); gy = ((cy[:, None] + Q[None, :, 1]) / RES).astype(int)
        inb = (gx >= 0) & (gx < like.shape[1]) & (gy >= 0) & (gy < like.shape[0])
        sc = np.where(inb, like[np.clip(gy, 0, like.shape[0] - 1), np.clip(gx, 0, like.shape[1] - 1)], 0).mean(1)
        i = int(sc.argmax()); best.append((float(sc[i]), cx[i], cy[i], math.degrees(th)))
    best.sort(reverse=True)
    print(f'{tag:8s} top: ' + '  '.join(f'score {b[0]:.3f} @ cell ({b[1]:.2f},{b[2]:.2f}) m yaw {b[3]:.0f}' for b in best[:3]))
