"""Timeline for the LIVE slice of a bag: phantom obstacle cells per second next to what the robot was doing (arm
joints moving, base turning / driving). Run while the bag's /joint_states is played on the isolated domain.
Usage: nvblox_phantom_timeline.py <out dir>"""
import math, os, sys, time

import numpy as np
import rclpy
from sensor_msgs.msg import JointState

OUT = sys.argv[1]
OCC, BODY = 0.03, 0.13
z = np.load(os.path.join(OUT, 'slices_live.npz'), allow_pickle=True); rows, grids = z['rows'], z['grids']
ok = np.isfinite(rows[:, 7]); rows, grids = rows[ok], grids[ok]
path = [(r[0], r[7], r[8]) for r in rows]; t0 = rows[0][0]
ph = {}
for k, (row, g) in enumerate(zip(rows, grids)):
    t, ox, oy, res, w, h, unk = row[:7]
    g = np.asarray(g, np.float32)
    ys, xs = np.nonzero((g != np.float32(unk)) & (g <= OCC))
    later = np.array([(px, py) for (pt, px, py) in path if pt > t + 1.0])
    n = 0
    if len(xs) and len(later):
        wx, wy = ox + (xs + 0.5) * res, oy + (ys + 0.5) * res
        n = int((np.sqrt((wx[:, None] - later[None, :, 0]) ** 2 + (wy[:, None] - later[None, :, 1]) ** 2).min(axis=1) <= BODY).sum())
    v = w_ = 0.0
    if 0 < k < len(rows) - 1:
        dt = rows[k + 1][0] - rows[k - 1][0]
        v = math.hypot(rows[k + 1][7] - rows[k - 1][7], rows[k + 1][8] - rows[k - 1][8]) / dt
        w_ = abs(math.atan2(math.sin(rows[k + 1][9] - rows[k - 1][9]), math.cos(rows[k + 1][9] - rows[k - 1][9]))) / dt
    ph[int(t - t0)] = (n, v, w_, len(xs))
rclpy.init(); node = rclpy.create_node('phantom_timeline'); js = []
node.create_subscription(JointState, '/joint_states', lambda m: js.append((m.header.stamp.sec + m.header.stamp.nanosec * 1e-9, list(m.position))), 50)
end, last_n, last_t = time.time() + 200, 0, time.time()
while time.time() < end:
    rclpy.spin_once(node, timeout_sec=0.5)
    if len(js) != last_n:
        last_n, last_t = len(js), time.time()
    elif js and time.time() - last_t > 6:
        break
arm = {}
for (ta, pa), (tb, pb) in zip(js, js[1:]):
    if tb > ta and len(pa) == len(pb):
        s = int(tb - t0)
        arm[s] = max(arm.get(s, 0.0), max(abs(b - a) for a, b in zip(pa, pb)) / (tb - ta))
print('s  phantom  obstacle_cells  base_m/s  base_rad/s  arm_rad/s(max joint)')
for s in sorted(ph):
    n, v, w_, tot = ph[s]
    print(f'{s:3d} {n:6d} {tot:8d} {v:10.2f} {w_:10.2f} {arm.get(s, float("nan")):10.2f}')
a = np.array([(ph[s][0], arm.get(s, 0.0), ph[s][2], ph[s][1]) for s in sorted(ph)])
mov = a[:, 1] > 0.05
print(f'seconds with the arm moving (> 0.05 rad/s): {int(mov.sum())} of {len(a)}; mean phantom cells: arm moving '
      f'{a[mov, 0].mean() if mov.any() else float("nan"):.1f}, arm still {a[~mov, 0].mean():.1f}; '
      f'base turning > 0.3 rad/s {a[a[:, 2] > 0.3, 0].mean() if (a[:, 2] > 0.3).any() else float("nan"):.1f}, '
      f'base still (v < 0.03, w < 0.1) {a[(a[:, 3] < 0.03) & (a[:, 2] < 0.1), 0].mean():.1f}')
