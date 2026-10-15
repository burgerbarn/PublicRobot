"""Clear-path check (owner rule 2026-09-29: the robot may only drive closer to an object if it can guarantee a clear
path on the floor). Wheels are never touched here; the arm only moves to the drive pose (and optionally look-down).

From the Aurora point cloud (/aurora/points2: the valid depth pixels, row-major, depth_camera_link) and the LiDAR (/scan), in base_link:
- fit the floor plane (RANSAC, normal within 10 deg of vertical) - measured, so it also works on the stand
- 5 cm grid over the floor; each cell is FLOOR (seen, flat within +-3 cm), OBSTACLE (anything 3 cm..0.5 m above the
  floor, or a LiDAR return), FLOOR ITEM (floor-level points inside an OWLv2 box for cables/clothes/etc. - too flat
  for the height test), DROP (more than 5 cm below the floor) or UNSEEN (too few depth points)
- corridor: robot width + 10 cm each side, from the robot's front along the target bearing to a stop point
  STOP_SHORT m before the target. CLEAR only if every corridor cell is FLOOR; unseen counts as not clear.
Saves ~/vision/floorcheck/<UTC>/: map.png (top-down), view.png (camera view coloured by class), result.json.
Usage (robot, ~/vision/venv, ROS sourced): python floor_check.py [--bearing 0] [--dist 2.0]
  The head turns toward --bearing (the travel direction) before looking: only the path about to be used is checked."""
import argparse, json, math, os, time

import cv2
import numpy as np
import PIL.Image
import rclpy
from rclpy.duration import Duration
from rclpy.time import Time
from sensor_msgs.msg import PointCloud2
from std_srvs.srv import SetBool, Trigger

from scan import HOME_PAN, Scan, make_detector, quat_to_R

HERE = os.path.dirname(os.path.abspath(__file__))
CELL = 0.05
X_RANGE, Y_RANGE = (-0.3, 4.3), (-1.6, 1.6)       # map extent in base_link (m)
FLOOR_TOL, OBST_MIN, OBST_MAX, DROP = 0.03, 0.03, 0.5, 0.05
MIN_PTS = 5                                        # depth points for a cell to count as seen
FOOT = (0.155, -0.15, 0.12, -0.115)                # nav2 footprint: front, back, left, right (m)
MARGIN = 0.10
STOP_SHORT = 1.0
UP = {2: 500, 3: 500, 4: 500}
LOOK_B = {2: 170, 3: 470, 4: 231}
DRIVE = {2: 793, 3: 51, 4: 160}
DEFAULT = {2: 860, 3: 45, 4: 178, 5: 500}
# owner-taught touch_your_toes (2026-09-29) with base 500 and wrist roll 500 (taught 484/496): camera 0.21 m ahead,
# -70 deg, sees the floor right in front. default<->toes model-checked clear (straight joint-space line, 81 samples)
TOES = {2: 254, 3: 255, 4: 157, 5: 500}
UNSEEN, FLOOR, OBST, DROPC, ITEM = 0, 1, 2, 3, 4
COL = {UNSEEN: (70, 70, 70), FLOOR: (60, 170, 60), OBST: (40, 40, 220), DROPC: (200, 60, 160), ITEM: (0, 150, 255)}
FLOOR_ITEMS = ['a cable', 'a wire', 'a shoe', 'a sock', 'clothes', 'a bag', 'a box', 'a toy', 'paper']   # no generic 'object on the floor': it boxes the whole rug


def cloud_xyz(m):
    off = {f.name: f.offset for f in m.fields}
    raw = np.frombuffer(m.data, np.uint8).reshape(-1, m.point_step)
    xyz = np.stack([raw[:, off[k]:off[k] + 4].copy().view(np.float32)[:, 0] for k in 'xyz'], 1)
    ok = np.isfinite(xyz).all(1) & (np.abs(xyz).sum(1) > 1e-6)
    return xyz, ok


def fit_floor(P, rng):
    """RANSAC plane with a near-vertical normal; returns (n, d) with n.z > 0 and n.p + d = height above floor."""
    best, best_n = None, 0
    for _ in range(300):
        a, b, c = P[rng.choice(len(P), 3, replace=False)]
        n = np.cross(b - a, c - a)
        if np.linalg.norm(n) < 1e-9:
            continue
        n /= np.linalg.norm(n)
        if abs(n[2]) < math.cos(math.radians(10)):
            continue
        n = n if n[2] > 0 else -n
        d = -n @ a
        k = int((np.abs(P @ n + d) < 0.015).sum())
        if k > best_n:
            best, best_n = (n, d), k
    if best is None:
        raise SystemExit('no floor plane found')
    inl = P[np.abs(P @ best[0] + best[1]) < 0.015]
    c = inl.mean(0); u, s, vt = np.linalg.svd(inl - c); n = vt[2]
    n = n if n[2] > 0 else -n
    return n, -n @ c, len(inl)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bearing', type=float, default=0.0, help='target bearing (deg, left +)')
    ap.add_argument('--dist', type=float, default=2.0, help='target distance from base_link (m)')
    ap.add_argument('--th', type=float, default=0.15, help='floor-item detector threshold')
    ap.add_argument('--no-feet', action='store_true', help='skip the look-down glance at the bumper strip')
    a = ap.parse_args()
    out = os.path.join(HERE, 'floorcheck', time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out)
    rclpy.init()
    n = Scan()
    n.create_subscription(PointCloud2, '/aurora/points2', lambda m: n.last.__setitem__('cloud', m), 2)
    n.cli['arm/drive'] = n.create_client(Trigger, '/rosorin_board_driver/arm/drive')
    n.spin_until(lambda: all(k in n.last for k in ('rgb', 'depth', 'info', 'cloud', 'scan', 'state')), 15, 'topics')
    s = n.state()
    if s['enabled'] or s['estop']:
        raise SystemExit(f'refusing: wheels enabled or e-stop: {s}')

    views = []                                      # (name, points base_link, valid mask, rgb)
    def grab(name):
        t0 = n.get_clock().now() + Duration(seconds=0.6)          # settle, then a cloud taken after it
        # the cloud is the valid depth pixels in row-major order (verified 2026-09-29: z identical) -> pair it with
        # the depth frame of the same stamp to know each point's pixel
        n.spin_until(lambda: Time.from_msg(n.last['cloud'].header.stamp) > t0 and 'depth' in n.last
                     and n.last['depth'].header.stamp == n.last['cloud'].header.stamp
                     and Time.from_msg(n.last['rgb'].header.stamp) > t0, 10, 'fresh cloud + depth')
        m, dep, rgb = n.last['cloud'], n.last['depth'], n.last['rgb']
        # arm joints arrive every 2 s while idle (driver joint refresh): spin until the chain to the camera exists
        for f in (m.header.frame_id, rgb.header.frame_id):
            n.spin_until(lambda: n.tf.can_transform('base_link', f, Time()), 10, 'camera TF')
        tf = n.tf.lookup_transform('base_link', m.header.frame_id, Time())
        R, t = quat_to_R(tf.transform.rotation), np.array([getattr(tf.transform.translation, k) for k in 'xyz'])
        tfr = n.tf.lookup_transform('base_link', rgb.header.frame_id, Time())
        Rr, tr = quat_to_R(tfr.transform.rotation), np.array([getattr(tfr.transform.translation, k) for k in 'xyz'])
        xyz, ok = cloud_xyz(m)
        D = np.frombuffer(dep.data, np.uint16).reshape(dep.height, dep.width)
        pix = np.flatnonzero(D > 0)
        if len(pix) != ok.sum():
            raise SystemExit(f'cloud/depth mismatch: {ok.sum()} points vs {len(pix)} depth pixels')
        img = np.frombuffer(rgb.data, np.uint8).reshape(rgb.height, rgb.width, -1)
        img = img.copy() if rgb.encoding == 'bgr8' else img[:, :, ::-1].copy()
        Pb = xyz[ok] @ R.T + t
        # each point's RGB pixel (for the appearance check)
        Pc = (Pb - tr) @ Rr; K = np.array(n.last['info'].k).reshape(3, 3)
        z = np.maximum(Pc[:, 2], 1e-3)
        uv = np.stack([K[0, 0] * Pc[:, 0] / z + K[0, 2], K[1, 1] * Pc[:, 1] / z + K[1, 2]], 1)
        views.append({'name': name, 'P': Pb, 'pix': pix, 'shape': D.shape, 'img': img, 'uv': uv, 'cam': (Rr, tr, K)})
        print(f'{name}: {len(Pb)} valid points of {len(ok)}', flush=True)

    r = n.call('arm/drive', Trigger.Request())
    print('arm/drive:', r.success, r.message[:120], flush=True)
    if not r.success:
        raise SystemExit('drive pose refused')
    # predictive glance (owner 2026-09-29): look where you are about to go - the head leads the body. Pan toward the
    # travel bearing (yaw = 1.0 + 0.24 (pan - 500) deg, measured), Wall-E style: small wind-up, overshoot, settle.
    # Drive pose model-checked clear over pan 34..966 at wrist 150/160/175 (2026-09-29).
    goal = max(34, min(966, int(round(HOME_PAN + (a.bearing - 1.0) / 0.24))))
    if not n.call('arm/enable', SetBool.Request(data=True)).success:
        raise SystemExit('arm enable refused')
    n.spin_until(lambda: (n.state() or {}).get('arm_enabled'), 5, 'arm enabled')
    try:
        cur = (n.state()['arm_pose'] or {}).get('1', HOME_PAN)
        d = 1 if goal > cur else -1
        if abs(goal - cur) > 20:
            n.pan(max(34, min(966, cur - 10 * d)), DRIVE)
            n.pan(max(34, min(966, goal + 8 * d)), DRIVE)
        n.pan(goal, DRIVE)
        grab(f'look_{a.bearing:+.0f}deg')
        if abs(a.bearing) < 1e-6 and not a.no_feet:
            # glance at my feet: the strip in front of the bumper the drive pose can't see. drive <-> look_down_b
            # direct is model-checked clear at pan 500 (2026-09-30) - only straight-ahead travel uses it for now
            n.pan(HOME_PAN, LOOK_B)
            grab('feet')
            n.pan(HOME_PAN, DRIVE)
    finally:
        n.call('arm/enable', SetBool.Request(data=False))
    scan = n.last['scan']
    n.spin_until(lambda: n.tf.can_transform('base_link', scan.header.frame_id, Time()), 10, 'laser TF')
    tfl = n.tf.lookup_transform('base_link', scan.header.frame_id, Time(), Duration(seconds=1))
    Rl, tl = quat_to_R(tfl.transform.rotation), np.array([getattr(tfl.transform.translation, k) for k in 'xyz'])

    # ---- floor plane PER VIEW: the arm model puts the camera ~3.6 cm differently in drive vs look-down
    # (2026-09-29: drive alone 9.2 cm, both views pooled 5.6 cm), so each view is measured against its own floor
    floors = []
    for v in views:
        P = v['P']
        fwd = P[(P[:, 0] > 0.1) & (P[:, 0] < 3.0) & (np.abs(P[:, 1]) < 1.5)]
        if len(fwd) < 2000 and floors:                      # too close for depth: use the first view's floor
            fn, fd, n_in = views[0]['plane'][0], views[0]['plane'][1], 0
        else:
            fn, fd, n_in = fit_floor(fwd[::max(1, len(fwd) // 20000)], np.random.default_rng(0))
        v['plane'] = (fn, fd)
        v['h'] = P @ fn + fd
        tilt = math.degrees(math.acos(fn[2]))
        floors.append({'view': v['name'], 'below_base_m': round(float(fd), 3), 'tilt_deg': round(tilt, 1)})
        print(f'{v["name"]} floor: {fd:+.3f} m below base_link origin (tilt {tilt:.1f} deg, {n_in} inliers)', flush=True)
    P = np.concatenate([v['P'] for v in views])

    # ---- appearance check: things on the floor too flat for the height test (cables, clothes, paper) -> OWLv2
    # boxes in the RGB frame; every floor-level point inside a box counts as an obstacle (conservative)
    det = make_detector('owlv2', '16', FLOOR_ITEMS)
    for v in views:
        H, W = v['img'].shape[:2]
        # a box over > 35 % of the frame is the scene, not an item (seen 2026-09-29: 'wire' over the whole rug)
        dets = [d for d in det(PIL.Image.fromarray(v['img'][:, :, ::-1]), a.th)
                if (d[2][2] - d[2][0]) * (d[2][3] - d[2][1]) < 0.35 * W * H]
        v['dets'] = dets
        hv = v['h']
        f = np.zeros(len(v['P']), bool)
        for _, _, bx in dets:
            f |= (v['uv'][:, 0] >= bx[0]) & (v['uv'][:, 0] <= bx[2]) & (v['uv'][:, 1] >= bx[1]) & (v['uv'][:, 1] <= bx[3])
        v['flagged'] = f & (hv < OBST_MAX)
        print(f'{v["name"]}: floor items ' + (', '.join(f'{nm} {sc:.2f}' for nm, sc, _ in dets) or 'none'), flush=True)
    # (Gemma strip check REMOVED 2026-09-30, owner: nothing in the driving path depends on bigbuddy. Flat items
    #  = the on-robot OWLv2 check above; depth + LiDAR for everything with height.)
    b = math.radians(a.bearing)
    stop = max(0.0, a.dist - STOP_SHORT)
    half = max(FOOT[2], -FOOT[3]) + MARGIN
    F = np.concatenate([v['flagged'] for v in views])

    # ---- grid
    nx, ny = int(round((X_RANGE[1] - X_RANGE[0]) / CELL)), int(round((Y_RANGE[1] - Y_RANGE[0]) / CELL))
    h = np.concatenate([v['h'] for v in views])
    keep = h < OBST_MAX
    ix = np.floor((P[:, 0] - X_RANGE[0]) / CELL).astype(int); iy = np.floor((P[:, 1] - Y_RANGE[0]) / CELL).astype(int)
    inb = keep & (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    cnt = np.zeros((nx, ny), int); hi = np.full((nx, ny), -9.0); lo = np.full((nx, ny), 9.0)
    np.add.at(cnt, (ix[inb], iy[inb]), 1)
    np.maximum.at(hi, (ix[inb], iy[inb]), h[inb]); np.minimum.at(lo, (ix[inb], iy[inb]), h[inb])
    # robust max: a cell is an obstacle only if >= 2 points are above OBST_MIN (single-pixel depth noise)
    above = np.zeros((nx, ny), int); sel = inb & (h > OBST_MIN); np.add.at(above, (ix[sel], iy[sel]), 1)
    below = np.zeros((nx, ny), int); sel = inb & (h < -DROP); np.add.at(below, (ix[sel], iy[sel]), 1)
    cls = np.full((nx, ny), UNSEEN, int)
    seen = cnt >= MIN_PTS
    cls[seen] = FLOOR
    cls[seen & (below >= 2)] = DROPC
    cls[(above >= 2)] = OBST
    item = np.zeros((nx, ny), int); sel = inb & F; np.add.at(item, (ix[sel], iy[sel]), 1)
    cls[(item >= MIN_PTS) & (cls != OBST)] = ITEM
    # LiDAR returns
    ang = scan.angle_min + np.arange(len(scan.ranges)) * scan.angle_increment
    rr = np.array(scan.ranges)
    okl = np.isfinite(rr) & (rr > max(scan.range_min, 0.12)) & (rr < scan.range_max)
    L = np.stack([rr[okl] * np.cos(ang[okl]), rr[okl] * np.sin(ang[okl]), np.zeros(okl.sum())], 1) @ Rl.T + tl
    lx = np.floor((L[:, 0] - X_RANGE[0]) / CELL).astype(int); ly = np.floor((L[:, 1] - Y_RANGE[0]) / CELL).astype(int)
    lin = (lx >= 0) & (lx < nx) & (ly >= 0) & (ly < ny)
    cls[lx[lin], ly[lin]] = OBST
    # robot footprint itself: not a corridor question
    cx = X_RANGE[0] + (np.arange(nx) + 0.5) * CELL; cy = Y_RANGE[0] + (np.arange(ny) + 0.5) * CELL
    CX, CY = np.meshgrid(cx, cy, indexing='ij')
    under = (CX <= FOOT[0]) & (CX >= FOOT[1]) & (CY <= FOOT[2]) & (CY >= FOOT[3])

    # ---- corridor along the target bearing (b, stop, half set above)
    along = CX * math.cos(b) + CY * math.sin(b)
    across = -CX * math.sin(b) + CY * math.cos(b)
    corr = (along >= 0) & (along <= stop + FOOT[0]) & (np.abs(across) <= half) & ~under
    cc = cls[corr]
    counts = {k: int((cc == v).sum()) for k, v in (('floor', FLOOR), ('obstacle', OBST), ('item', ITEM), ('drop', DROPC), ('unseen', UNSEEN))}
    first_block = None
    blk = corr & ((cls == OBST) | (cls == DROPC) | (cls == ITEM))
    if blk.any():
        first_block = round(float(along[blk].min()), 2)
    uns = corr & (cls == UNSEEN)
    unseen_span = [round(float(along[uns].min()), 2), round(float(along[uns].max()), 2)] if uns.any() else None
    verdict = 'CLEAR' if counts['obstacle'] == counts['item'] == counts['drop'] == counts['unseen'] == 0 else \
              'BLOCKED' if first_block is not None else 'NOT CONFIRMED (unseen floor)'
    res = {'target': {'bearing_deg': a.bearing, 'dist_m': a.dist}, 'stop_at_m': round(stop, 2),
           'corridor_width_m': round(2 * half, 2), 'verdict': verdict, 'cells': counts,
           'first_obstacle_along_m': first_block, 'unseen_along_m': unseen_span,
           'floors': floors,
           'views': [v['name'] for v in views],
           'floor_items': {v['name']: [(nm, round(sc, 3)) for nm, sc, _ in v['dets']] for v in views}}
    json.dump(res, open(os.path.join(out, 'result.json'), 'w'), indent=1)
    print(json.dumps(res), flush=True)

    # ---- top-down map: x forward = up, left = left
    mp = np.zeros((nx * 10, ny * 10, 3), np.uint8)
    for i in range(nx):
        for j in range(ny):
            r0, c0 = (nx - 1 - i) * 10, (ny - 1 - j) * 10
            col = np.array(COL[cls[i, j]], float)
            if corr[i, j]:
                col = col * 0.55 + np.array([255, 255, 255]) * 0.45
            mp[r0:r0 + 10, c0:c0 + 10] = col.astype(np.uint8)
    def px(x, y):
        return int((ny - (y - Y_RANGE[0]) / CELL) * 10), int((nx - (x - X_RANGE[0]) / CELL) * 10)
    for d in range(1, 5):
        cv2.putText(mp, f'{d} m', (4, px(d, 0)[1] - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1)
        cv2.line(mp, (0, px(d, 0)[1]), (22, px(d, 0)[1]), (230, 230, 230), 1)
    fp = [px(FOOT[0], FOOT[2]), px(FOOT[0], FOOT[3]), px(FOOT[1], FOOT[3]), px(FOOT[1], FOOT[2])]
    cv2.polylines(mp, [np.array(fp)], True, (0, 220, 255), 2)
    tx, ty = a.dist * math.cos(b), a.dist * math.sin(b)
    cv2.drawMarker(mp, px(tx, ty), (0, 220, 255), cv2.MARKER_STAR, 22, 2)
    for x, y, _ in L:
        if X_RANGE[0] < x < X_RANGE[1] and Y_RANGE[0] < y < Y_RANGE[1]:
            cv2.circle(mp, px(x, y), 2, (255, 255, 255), -1)
    leg = [('floor', FLOOR), ('obstacle', OBST), ('floor item', ITEM), ('drop', DROPC), ('unseen', UNSEEN)]
    for i, (nm, v) in enumerate(leg):
        cv2.rectangle(mp, (mp.shape[1] - 110, 8 + 18 * i), (mp.shape[1] - 96, 20 + 18 * i), COL[v], -1)
        cv2.putText(mp, nm, (mp.shape[1] - 90, 19 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (230, 230, 230), 1)
    cv2.putText(mp, verdict, (8, mp.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.imwrite(os.path.join(out, 'map.png'), mp)

    # ---- camera views coloured by per-point class (organized cloud -> image), next to the RGB frame
    for v in views:
        Pv, hv = v['P'], v['h']
        c = np.full((len(Pv), 3), COL[FLOOR], np.uint8)
        c[hv > OBST_MIN] = COL[OBST]
        c[hv >= OBST_MAX] = (120, 90, 40)                          # above robot height: ignored
        c[hv < -DROP] = COL[DROPC]
        c[v['flagged']] = COL[ITEM]
        vx = Pv[:, 0] * math.cos(b) + Pv[:, 1] * math.sin(b); vy = -Pv[:, 0] * math.sin(b) + Pv[:, 1] * math.cos(b)
        inc = (vx >= 0) & (vx <= stop + FOOT[0]) & (np.abs(vy) <= half)
        c[inc] = (c[inc] * 0.55 + 115).astype(np.uint8)            # corridor brighter
        cimg = np.full((*v['shape'], 3), COL[UNSEEN], np.uint8).reshape(-1, 3)
        cimg[v['pix']] = c
        cimg = cimg.reshape(*v['shape'], 3)
        img = v['img'].copy()
        for nm, sc, bx in v['dets']:
            x0, y0, x1, y1 = [int(q) for q in bx]
            cv2.rectangle(img, (x0, y0), (x1, y1), COL[ITEM], 2)
            cv2.putText(img, f'{nm} {sc:.2f}', (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COL[ITEM], 1)
        for (x0, y0, x1, y1), ok, what in v.get('strips', []):
            col = (255, 255, 255) if ok else (0, 0, 255)
            cv2.rectangle(img, (x0, y0), (x1, y1), col, 1)
            cv2.putText(img, 'clear' if ok else what[:24], (x0 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1)
        img = cv2.resize(img, (int(img.shape[1] * v['shape'][0] / img.shape[0]), v['shape'][0]))
        cv2.imwrite(os.path.join(out, f'view_{v["name"]}.png'), np.hstack([img, cimg]))
    print('saved', out, flush=True)
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    from mind_pause import mind_paused
    with mind_paused('floor check'):              # the robot mind hands over the arm, gets it back after
        main()
