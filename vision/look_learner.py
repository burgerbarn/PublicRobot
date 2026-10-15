"""What the robot's looks are worth - learned from the looks the mind makes itself (owner 2026-10-02: "give the robot
the capabilities to learn with its arm"; no scripted scans). The mind moves the head its own smooth way and calls
record() whenever a look has settled; this keeps, per head direction (the mind's own memory cell):
  what the look showed  - 3D cells (5 cm) of the room new in this look-around, floor area seen, valid depth
  what the look cost    - share of LiDAR returns left while the head is there (the arm passes in front of the LiDAR)
value() is that knowledge as one number the mind can weigh when it chooses where to glance next.
Model: ~/practice/head_model.json. No motion happens here."""
import json, os, time

import numpy as np
from rclpy.time import Time

MODEL = os.path.expanduser('~/practice/head_model.json')
VOX = 0.05


class LookLearner:
    def __init__(self):
        try:
            self.model = json.load(open(MODEL))
        except (OSError, ValueError):
            self.model = {'looks': {}}
        self.model.setdefault('looks', {})
        self.seen, self.saved = set(), 0.0

    def new_session(self):
        self.seen = set()                                    # "new" is counted within one look-around

    def record(self, key, pan, wr, depth, info, tf, scan):
        if depth is None or info is None:
            return None
        t = tf.lookup_transform('base_link', info.header.frame_id, Time()).transform
        q = t.rotation; x, y, z, w = q.x, q.y, q.z, q.w
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        d = np.frombuffer(depth.data, np.uint16).reshape(depth.height, depth.step // 2)[:, :depth.width][::4, ::4] / 1000.0
        v, u = np.mgrid[0:depth.height:4, 0:depth.width:4]
        ok = d > 0
        if not ok.any():
            return None
        K = info.k
        P = np.stack([(u[ok] - K[2]) / K[0] * d[ok], (v[ok] - K[5]) / K[4] * d[ok], d[ok]], -1) @ R.T + \
            np.array([t.translation.x, t.translation.y, t.translation.z])
        cells = set(map(tuple, np.floor(P / VOX).astype(np.int32)))
        new = len(cells - self.seen); self.seen |= cells
        zs = P[:, 2]; low = P[zs < np.percentile(zs, 3) + 0.05]
        floor_m2 = len(set(map(tuple, np.floor(low[:, :2] / VOX).astype(np.int32)))) * VOX * VOX
        lidar = None
        if scan is not None:
            r = np.asarray(scan.ranges, np.float32); lidar = float((np.isfinite(r) & (r > 0.12)).mean())
        m = self.model['looks'].setdefault(key, {'n': 0})
        n = m['n']
        for k, val in (('new_cells', new), ('cells_seen', len(cells)), ('floor_m2', floor_m2),
                       ('depth_valid', float(ok.mean())), ('lidar_valid', lidar)):
            if val is not None:
                m[k] = round((m.get(k, val) * min(n, 9) + val) / (min(n, 9) + 1), 3)      # running mean, recent-weighted
        m['n'], m['pan'], m['wrist'], m['t'] = n + 1, int(pan), int(wr), round(time.time())
        self.model['best_new_cells'] = max(v.get('new_cells', 0) for v in self.model['looks'].values())
        if time.time() - self.saved > 5:
            self.save()
        return dict(cell=key, new_cells=new, floor_m2=round(floor_m2, 2), lidar_valid=None if lidar is None else round(lidar, 3))

    def save(self):
        os.makedirs(os.path.dirname(MODEL), exist_ok=True)
        self.model['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        json.dump(self.model, open(MODEL + '.tmp', 'w'), indent=1); os.replace(MODEL + '.tmp', MODEL)
        self.saved = time.time()

    def value(self, key):
        """0..1: how much a look there has been worth (what it shows minus the LiDAR it takes). Unknown = 1."""
        m = self.model['looks'].get(key)
        if not m:
            return 1.0
        best = max(1.0, self.model.get('best_new_cells', 1.0))
        base = max((v.get('lidar_valid', 0.0) for v in self.model['looks'].values()), default=0.0)
        cost = max(0.0, base - m.get('lidar_valid', base)) / base if base > 0 else 0.0
        return float(max(0.0, min(1.0, m.get('new_cells', 0.0) / best - cost)))
