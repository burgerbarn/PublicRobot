"""Phantom obstacles in nvblox's slice: obstacle cells that lie on floor the robot itself drove over later in the same
run (so nothing solid was there). Input: slices_<name>.npz from nvblox_slice_recorder.py (replays, or the slice that
was recorded live in the bag). Usage: nvblox_phantom_analyze.py <out dir> [names...]"""
import math, os, sys

import numpy as np

OUT = sys.argv[1]
NAMES = sys.argv[2:] or ['live', 'stock', 'slow', 'none']
OCC, CELL, BODY = 0.03, 0.05, 0.13          # obstacle = slice distance <= 3 cm; body half-width 13 cm

print(f'{"slice":6s} {"snapshots":>9s} | snapshots with phantom cells on the path it drove later | phantom cells per snapshot: '
      f'{"mean":>5s} {"max":>5s} | distance camera -> phantom (m): {"median":>6s} {"p10":>5s} {"min":>5s} | '
      f'path blocked within 1.0 m ahead (snapshots)')
for name in NAMES:
    f = os.path.join(OUT, f'slices_{name}.npz')
    if not os.path.exists(f):
        print(f'{name:6s} no data'); continue
    z = np.load(f, allow_pickle=True); rows, grids = z['rows'], z['grids']
    ok = np.isfinite(rows[:, 7])
    rows, grids = rows[ok], grids[ok]
    if len(rows) < 5:
        print(f'{name:6s} too few snapshots with a pose ({len(rows)})'); continue
    path = [(r[0], r[7], r[8]) for r in rows]
    n_ph, dists, with_ph, near = [], [], 0, 0
    for k, (row, g) in enumerate(zip(rows, grids)):
        t, ox, oy, res, w, h, unk = row[:7]
        g = np.asarray(g, np.float32)
        ys, xs = np.nonzero((g != np.float32(unk)) & (g <= OCC))
        if not len(xs):
            n_ph.append(0); continue
        wx, wy = ox + (xs + 0.5) * res, oy + (ys + 0.5) * res
        later = np.array([(px, py) for (pt, px, py) in path if pt > t + 1.0])
        if not len(later):
            continue
        d = np.sqrt((wx[:, None] - later[None, :, 0]) ** 2 + (wy[:, None] - later[None, :, 1]) ** 2)   # cell -> later robot centres
        ph = d.min(axis=1) <= BODY
        n_ph.append(int(ph.sum()))
        if ph.any():
            with_ph += 1
            cx, cy = (row[10], row[11]) if np.isfinite(row[10]) else (row[7], row[8])
            dc = np.hypot(wx[ph] - cx, wy[ph] - cy)
            dists.extend(dc.tolist())
            if (np.hypot(wx[ph] - row[7], wy[ph] - row[8]) <= 1.0).any():
                near += 1
    dists = np.array(dists) if dists else np.array([np.nan])
    print(f'{name:6s} {len(rows):9d} | {with_ph:4d} of {len(n_ph):4d} ({100 * with_ph / max(1, len(n_ph)):3.0f} %)'
          f'{"":32s} | {"":27s} {np.mean(n_ph):5.1f} {np.max(n_ph):5d} | {"":31s} {np.nanmedian(dists):6.2f} '
          f'{np.nanpercentile(dists, 10):5.2f} {np.nanmin(dists):5.2f} | {near}')
