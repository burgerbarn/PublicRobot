"""How well does the scan-fit check tell a right pose from a wrong one? Offline, from a recorded run on the kept map
(bag with /map /scan /tf /tf_static): for scans along the run, the fit at the pose the robot had (map -> base_link from
the bag's TF), at shifted/turned poses, and what the whole-map search of nav_supervisor returns (place found, error,
runner-up). Sets accept_fit / search_margin / lost_fit of rosorin_base/nav_supervisor.py from data.
Usage: scan_fit_replay.py <bag dir> [every Nth scan, default 5] [search every Mth evaluated scan, default 4]"""
import math, sys, time

import numpy as np
import rosbag2_py
from rclpy.duration import Duration
from rclpy.serialization import deserialize_message
from rclpy.time import Time
from rosidl_runtime_py.utilities import get_message
from tf2_ros import Buffer

from rosorin_base.nav_supervisor import ScanMatcher, yaw_of

ARG = [a for a in sys.argv[1:] if a != '-v' and not a.startswith('--')]
BAG = ARG[0]; NTH = int(ARG[1]) if len(ARG) > 1 else 5; MTH = int(ARG[2]) if len(ARG) > 2 else 4
r = rosbag2_py.SequentialCompressionReader()        # the runs are recorded with zstd file compression
r.open(rosbag2_py.StorageOptions(uri=BAG, storage_id='sqlite3'), rosbag2_py.ConverterOptions('cdr', 'cdr'))
types = {t.name: get_message(t.type) for t in r.get_all_topics_and_types()}
r.set_filter(rosbag2_py.StorageFilter(topics=['/map', '/scan', '/tf', '/tf_static']))
buf = Buffer(cache_time=Duration(seconds=100000.0)); scans = []; grid = None
while r.has_next():
    topic, data, t = r.read_next()
    m = deserialize_message(data, types[topic])
    if topic == '/map':
        grid = grid or m
    elif topic == '/scan':
        scans.append(m)
    else:
        for tr in m.transforms:
            (buf.set_transform_static if topic == '/tf_static' else buf.set_transform)(tr, 'bag')
MAPBAG = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--map-from=')), None)
if MAPBAG:          # score the same scans against ANOTHER map (the last /map of a mapping run): is the kept map stale?
    # a single .db3 file (a run that was cut off leaves no metadata.yaml) or a bag directory
    r2 = rosbag2_py.SequentialReader() if MAPBAG.endswith('.db3') else rosbag2_py.SequentialCompressionReader()
    r2.open(rosbag2_py.StorageOptions(uri=MAPBAG, storage_id='sqlite3'), rosbag2_py.ConverterOptions('cdr', 'cdr'))
    r2.set_filter(rosbag2_py.StorageFilter(topics=['/map']))
    while r2.has_next():
        _, data, _ = r2.read_next(); grid = deserialize_message(data, types['/map'])
    print(f'map taken from {MAPBAG} (poses of the run are in the other map frame: only search fit / runner-up / walls count)')
M = ScanMatcher(grid)
print(f'{len(scans)} scans, map {grid.info.width}x{grid.info.height}, {len(M.free)} free cells')
lt = buf.lookup_transform('base_link', scans[0].header.frame_id, Time()).transform
lx, ly, lyaw = lt.translation.x, lt.translation.y, yaw_of(lt.rotation)
rows, srows, last = [], [], None
SHIFTS = [('0.2 m', 0.2, 0), ('0.5 m', 0.5, 0), ('1.0 m', 1.0, 0), ('10 deg', 0, 10), ('20 deg', 0, 20), ('45 deg', 0, 45),
          ('180 deg', 0, 180)]
rng = np.random.default_rng(1)
for k, s in enumerate(scans[::NTH]):
    try:
        tf = buf.lookup_transform('map', 'base_link', Time.from_msg(s.header.stamp)).transform
    except Exception:
        continue
    p = np.array([tf.translation.x, tf.translation.y, yaw_of(tf.rotation)])
    rr = np.asarray(s.ranges, np.float32); ok = np.isfinite(rr) & (rr > max(s.range_min, 0.12)) & (rr < s.range_max)
    if ok.sum() < 60:
        continue
    a = s.angle_min + np.arange(len(rr)) * s.angle_increment
    a = a[ok] + lyaw; pts = np.stack([lx + rr[ok] * np.cos(a), ly + rr[ok] * np.sin(a)], 1)
    moved = 0.0 if last is None else math.hypot(p[0] - last[0], p[1] - last[1]) + abs(math.remainder(p[2] - last[2], math.tau))
    last = p
    f0 = float(M.fits(p[None], pts)[0]); _, fr = M.refine(p, pts)
    wrong = []
    for name, d, deg in SHIFTS:
        th = rng.uniform(0, math.tau)
        q = p + np.array([d * math.cos(th), d * math.sin(th), math.radians(deg) * rng.choice([-1, 1])])
        wrong.append(float(M.fits(q[None], pts)[0]))
    rows.append([f0, fr, moved, int(ok.sum())] + wrong)
    if k % MTH == 0:
        t0 = time.time(); sp, sf, s2 = M.search(pts)
        err = math.hypot(sp[0] - p[0], sp[1] - p[1]); ey = abs(math.degrees(math.remainder(sp[2] - p[2], math.tau)))
        tw = M.through_walls(np.array([p, M.refine(p, pts)[0], sp]), pts, (lx, ly))
        srows.append([sf, s2, err, ey, f0, time.time() - t0, tw[0], tw[1], tw[2]])
        if '-v' in sys.argv:
            print(f'  t {k * NTH / 10:5.1f}s recorded ({p[0]:5.2f},{p[1]:5.2f},{math.degrees(p[2]):4.0f}) fit {f0:.2f} | search ({sp[0]:5.2f},{sp[1]:5.2f},{math.degrees(sp[2]) % 360:4.0f}) fit {sf:.2f} next {s2:.2f} | apart {err:.2f} m {ey:.0f} deg | beams through walls: recorded {tw[0]:.2f} refined {tw[1]:.2f} search {tw[2]:.2f}')
R = np.array(rows); S = np.array(srows)
q = lambda v: ' '.join(f'{x:.2f}' for x in np.percentile(v, [1, 5, 25, 50, 75, 95]))
print(f'{len(R)} scans evaluated; valid points per scan median {np.median(R[:, 3]):.0f}')
print('fit percentiles                 1%   5%   25%  50%  75%  95%')
print(f'  at the recorded pose        {q(R[:, 0])}')
print(f'  after local refine          {q(R[:, 1])}')
still = R[:, 2] < 0.005
print(f'  recorded pose, standing     {q(R[still, 0]) if still.sum() > 5 else "-"}   ({still.sum()} scans)')
print(f'  recorded pose, moving       {q(R[~still, 0]) if (~still).sum() > 5 else "-"}   ({(~still).sum()} scans)')
for j, (name, _, _) in enumerate(SHIFTS):
    print(f'  pose wrong by {name:8s}      {q(R[:, 4 + j])}')
if len(S):
    good = (S[:, 2] < 0.3) & (S[:, 3] < 20)
    print(f'whole-map search on {len(S)} scans: right place {good.sum()} ({100 * good.mean():.0f} %), median {np.median(S[:, 5]):.1f} s')
    print(f'  when right: fit {q(S[good, 0])} | margin over runner-up {q(S[good, 0] - S[good, 1])}')
    if (~good).any():
        print(f'  when WRONG: fit {q(S[~good, 0])} | margin {q(S[~good, 0] - S[~good, 1])} | fit at true pose {q(S[~good, 4])}')
    for acc, mar in ((0.75, 0.05), (0.70, 0.05), (0.70, 0.10), (0.65, 0.10), (0.60, 0.10), (0.60, 0.15)):
        take = (S[:, 0] >= acc) & (S[:, 0] - S[:, 1] >= mar)
        print(f'  rule fit>={acc:.2f} & margin>={mar:.2f}: accepts {take.sum():3d}/{len(S)}, of those wrong {int((take & ~good).sum())}')
