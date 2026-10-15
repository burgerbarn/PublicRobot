"""Analysis of nvblox_decay_replay.sh output: for each decay setting, how long an obstacle cell stays in the slice
after the camera stopped looking at it, how many obstacle cells the slice holds over the run, and a picture of the
final slice. Usage: nvblox_decay_analyze.py <out dir> [names...]   (run with the vision venv: numpy + cv2)"""
import math, os, sys

import cv2
import numpy as np

OUT = sys.argv[1]
NAMES = sys.argv[2:] or ['stock', 'slow', 'none']
OCC = 0.03            # m: slice distance at or below this = obstacle cell (voxel 0.05 m)
HFOV, RMIN, RMAX = math.radians(37), 0.25, 3.5      # "is the camera looking there": +-37 deg (fx 419.9, 640 px), 0.25-3.5 m
CELL = 0.05


def load(name):
    z = np.load(os.path.join(OUT, f'slices_{name}.npz'), allow_pickle=True)
    return z['rows'], z['grids']


def cells(row, g):
    """Obstacle cells and known cells of one slice as sets of world cell indices."""
    t, ox, oy, res, w, h, unk = row[:7]
    known = g != np.float32(unk)
    occ = known & (g <= OCC)
    def idx(mask):
        ys, xs = np.nonzero(mask)
        wx, wy = ox + (xs + 0.5) * res, oy + (ys + 0.5) * res
        return set(zip(np.round(wx / CELL).astype(int).tolist(), np.round(wy / CELL).astype(int).tolist()))
    return idx(occ), idx(known)


def in_view(cell, row):
    cx, cy, cyaw = row[10:13]
    if not np.isfinite(cyaw):
        return False
    dx, dy = cell[0] * CELL - cx, cell[1] * CELL - cy
    r = math.hypot(dx, dy)
    return RMIN <= r <= RMAX and abs(math.atan2(math.sin(math.atan2(dy, dx) - cyaw), math.cos(math.atan2(dy, dx) - cyaw))) <= HFOV


print(f'{"setting":8s} {"slices":>6s} {"run s":>6s} | obstacle cells: {"mean":>5s} {"end":>5s} {"ever":>5s} | '
      f'kept after last look (s): {"median":>6s} {"p90":>6s} {"max":>6s} | still there at the end, unseen > 20 s')
for name in NAMES:
    try:
        rows, grids = load(name)
    except Exception as e:
        print(f'{name:8s} no data ({e})'); continue
    if not len(rows):
        print(f'{name:8s} no slices'); continue
    t0 = rows[0][0]
    last_seen, last_occ, ever, counts = {}, {}, set(), []
    for row, g in zip(rows, grids):
        t = row[0] - t0
        occ, known = cells(row, np.asarray(g, np.float32))
        counts.append(len(occ))
        for c in occ:
            last_occ[c] = t; ever.add(c)
        for c in ever:                                        # is the camera looking at this (former) obstacle now?
            if in_view(c, row):
                last_seen[c] = t
    T = rows[-1][0] - t0
    occ_end, known_end = cells(rows[-1], np.asarray(grids[-1], np.float32))
    kept = [last_occ[c] - last_seen[c] for c in ever if c in last_seen and last_seen[c] < T - 1.0]
    kept = np.array([max(0.0, k) for k in kept]) if kept else np.array([0.0])
    unseen20 = sum(1 for c in occ_end if c in last_seen and T - last_seen[c] > 20.0)
    print(f'{name:8s} {len(rows):6d} {T:6.0f} | {"":15s} {np.mean(counts):5.0f} {len(occ_end):5d} {len(ever):5d} | '
          f'{"":24s} {np.median(kept):6.1f} {np.percentile(kept, 90):6.1f} {kept.max():6.1f} | {unseen20}')
    # picture of the final slice: obstacle black, seen-free white, unknown grey, robot path red, robot end blue
    xs = [c[0] for c in known_end | ever]; ys = [c[1] for c in known_end | ever]
    if not xs:
        continue
    x0, x1, y0, y1 = min(xs) - 5, max(xs) + 5, min(ys) - 5, max(ys) + 5
    img = np.full((y1 - y0 + 1, x1 - x0 + 1, 3), 128, np.uint8)
    for c in known_end:
        img[c[1] - y0, c[0] - x0] = (255, 255, 255)
    for c in ever - occ_end:
        img[c[1] - y0, c[0] - x0] = (200, 220, 255)          # was an obstacle at some time, gone at the end
    for c in occ_end:
        img[c[1] - y0, c[0] - x0] = (0, 0, 0)
    for row in rows:
        if np.isfinite(row[7]):
            px, py = int(round(row[7] / CELL)) - x0, int(round(row[8] / CELL)) - y0
            if 0 <= py < img.shape[0] and 0 <= px < img.shape[1]:
                img[py, px] = (0, 0, 255)
    if np.isfinite(rows[-1][7]):
        cv2.circle(img, (int(round(rows[-1][7] / CELL)) - x0, int(round(rows[-1][8] / CELL)) - y0), 3, (255, 0, 0), -1)
    img = cv2.resize(cv2.flip(img, 0), None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(os.path.join(OUT, f'final_{name}.png'), img)
