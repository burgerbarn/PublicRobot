"""For the snapshots with the most phantom obstacle cells (nvblox_phantom_analyze.py's definition) in the LIVE slice of
a bag: where the phantom cells are in the robot's frame, how the robot was moving, and the camera frame at that
moment (saved as JPG while the bag's RGB topic is played on the isolated domain).
Usage: nvblox_phantom_frames.py <out dir> [n]   then play the bag: ros2 bag play <bag> --topics /aurora/rgb/image_raw"""
import math, os, sys, time

import numpy as np

OUT, N = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5
OCC, BODY = 0.03, 0.13
z = np.load(os.path.join(OUT, 'slices_live.npz'), allow_pickle=True); rows, grids = z['rows'], z['grids']
ok = np.isfinite(rows[:, 7]); rows, grids = rows[ok], grids[ok]
path = [(r[0], r[7], r[8]) for r in rows]
found = []
for k, (row, g) in enumerate(zip(rows, grids)):
    t, ox, oy, res, w, h, unk = row[:7]
    g = np.asarray(g, np.float32)
    ys, xs = np.nonzero((g != np.float32(unk)) & (g <= OCC))
    later = np.array([(px, py) for (pt, px, py) in path if pt > t + 1.0])
    if not len(xs) or not len(later):
        continue
    wx, wy = ox + (xs + 0.5) * res, oy + (ys + 0.5) * res
    ph = np.sqrt((wx[:, None] - later[None, :, 0]) ** 2 + (wy[:, None] - later[None, :, 1]) ** 2).min(axis=1) <= BODY
    if not ph.any():
        continue
    c, s = math.cos(row[9]), math.sin(row[9])
    fx = c * (wx[ph] - row[7]) + s * (wy[ph] - row[8]); fy = -s * (wx[ph] - row[7]) + c * (wy[ph] - row[8])
    v = w_ = float('nan')
    if 0 < k < len(rows) - 1:
        dt = rows[k + 1][0] - rows[k - 1][0]
        v = math.hypot(rows[k + 1][7] - rows[k - 1][7], rows[k + 1][8] - rows[k - 1][8]) / dt
        w_ = math.atan2(math.sin(rows[k + 1][9] - rows[k - 1][9]), math.cos(rows[k + 1][9] - rows[k - 1][9])) / dt
    found.append((int(ph.sum()), t, fx.min(), fx.max(), fy.min(), fy.max(), v, w_, t - rows[0][0]))
found.sort(reverse=True)
picks = []
for f in found:                                              # the worst ones, at least 5 s apart
    if all(abs(f[1] - p[1]) > 5 for p in picks):
        picks.append(f)
    if len(picks) == N:
        break
print('phantom cells | s into run | ahead of the robot (m) | left of the robot (m) | robot speed m/s, turn rad/s')
for n_, t, a0, a1, l0, l1, v, w_, rel in picks:
    print(f'{n_:13d} | {rel:10.1f} | {a0:+.2f} .. {a1:+.2f}{"":9s} | {l0:+.2f} .. {l1:+.2f}{"":8s} | {v:.2f}, {w_:+.2f}')
import cv2, rclpy
from rclpy.parameter import Parameter
from sensor_msgs.msg import Image
rclpy.init(); n = rclpy.create_node('phantom_frames'); best = {}


def on_img(m):
    t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
    for i, p in enumerate(picks):
        d = abs(t - p[1])
        if d < 0.6 and d < best.get(i, (9, None))[0]:
            img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)[:, :, :3]
            best[i] = (d, img if m.encoding == 'bgr8' else img[:, :, ::-1].copy())


n.create_subscription(Image, '/aurora/rgb/image_raw', on_img, 20)
end, last = time.time() + 200, None
while time.time() < end:
    rclpy.spin_once(n, timeout_sec=0.5)
    if len(best) == len(picks) and last is None:
        last = time.time()
    if last and time.time() - last > 8:
        break
for i, (d, img) in sorted(best.items()):
    cv2.imwrite(os.path.join(OUT, f'phantom_{i}_{picks[i][8]:.0f}s.jpg'), img)
print('frames saved:', len(best))
