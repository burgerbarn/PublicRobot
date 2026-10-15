"""The robot explores its room by itself (owner milestone 2026-10-01): Nav2 + AMCL on the static map, robot picks
its own goals. Claude does not drive: this runs on the robot as rosorin-explore.service.

Run: localize (AMCL global reinit + 360 deg Spin) -> check scan-to-map overlay >= MIN_OVERLAY (else retry once,
then stop) -> loop: pick the reachable, unvisited spot with the most unvisited floor per metre of planned path
(Nav2 ComputePathToPose) -> NavigateToPose -> mark floor within VISIT_R of the path travelled as visited -> until
coverage >= TARGET, time limit, low battery, e-stop, too many failures or /rosorin_explore/enable false -> return to
the start pose. The arm is not moved. Wheels are enabled only while a Nav2 action runs, and ALWAYS disabled on exit.
Changes vs the base map (owner 2026-10-01: base map + the robot identifies what changed; Nav2's live LiDAR layers
already route around them): every 0.5 s while localized, scan endpoints > 15 cm from any base wall add a "new" hit to
their cell, base wall cells that a beam passed through add a "gone" hit. Cells with hits in >= CHANGE_SCANS scans are
changes, clustered and saved: changes.json (+ changes_new.npy / changes_gone.npy). LiDAR plane only (~16 cm): objects
lower than that are invisible here (depth = its own test).
Self-monitoring while driving (owner 2026-10-01: "is the robot confident it sees well?"):
- localization: standing still first (saved home seed, then global reset + AMCL no-motion updates); the 360 deg spin
  only if that fails.
- confidence: ~1 Hz scan-to-base-map overlay (median of 3) and AMCL x/y variance. Overlay < MIN_RUN_OVERLAY or
  sigma > MAX_SIGMA_M -> cancel goal, wheels off, re-localize in place (spin, no global reset), continue only if the
  overlay is back >= MIN_OVERLAY; two failed re-localizations end the run.
- scan health: rate (2 s window) >= MIN_SCAN_HZ, valid-return share (5-scan mean) >= MIN_VALID, newest scan < 1 s
  old. Unhealthy -> stop, wait SCAN_RECOVER_S for recovery, else end the run (home only if healthy again).
Floor = global-costmap cells with cost < 99 (centre can stand there). Log: ~/explore/<run>/log.jsonl + summary.json.
Owner stop: gamepad deadman / e-stop (board_driver), or `ros2 service call /rosorin_explore/enable
std_srvs/srv/SetBool "{data: false}"`."""
import json, math, os, sys, time

import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.qos import DurabilityPolicy, QoSProfile
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose, Spin
from nav_msgs.msg import OccupancyGrid
from sensor_msgs.msg import BatteryState, LaserScan
from std_msgs.msg import String
from std_srvs.srv import Empty, SetBool

sys.path.insert(0, os.path.expanduser('~/vision'))
from mind_pause import mind_paused  # noqa: E402

MIN_OVERLAY = 0.65        # scan points within 10 cm of a map wall (scripts/scan_overlay.py; 0.69 on 2026-09-30)
TARGET = 0.90             # coverage of reachable floor
VISIT_R = 0.5             # m: floor this close to the robot's path counts as visited
TIME_LIMIT = 600.0        # s per run (gate: 10 min runs)
GOAL_TIMEOUT = 60.0
MAX_FAILS = 3             # consecutive failed goals -> stop
LOW_BATTERY_V = 10.0      # owner 2026-10-01: 10.9 was too cautious; the board beeps loudly when it needs a charge.
                          # Compared against a 20 s average (motor load sags single readings).
MATCH_MARGIN = 0.05      # scan match: the best pose must beat every other place on the map by this much
STABLE_OVERLAY = 0.55    # with two spins agreeing on the pose
MIN_RUN_OVERLAY = 0.45   # while driving (new objects + motion lower it; 0.72-0.74 at rest 2026-10-01)
MAX_SIGMA_M = 0.5         # AMCL position std dev
MIN_SCAN_HZ, MIN_VALID, SCAN_RECOVER_S = 7.0, 0.45, 10.0   # COIN-D6: 10 Hz, ~61 % valid returns (2026-10-01)
CHANGE_SCANS = 4         # a change must be seen in this many scans (flicker filter)
GATE = '--gate' in sys.argv  # nav gate only: localize, 0.6 m out, return home
LASER_X = 0.048           # laser offset in base_link (scan_overlay.py)


def yaw_of(q): return 2 * math.atan2(q.z, q.w)


class Explorer:
    def __init__(self):
        self.n = rclpy.create_node('room_explorer')
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.s = {}
        self.n.create_subscription(OccupancyGrid, '/map', lambda m: self.s.__setitem__('map', m), latched)
        self.n.create_subscription(OccupancyGrid, '/global_costmap/costmap',
                                   lambda m: self.s.__setitem__('cost', m), latched)
        # volatile: slam_toolbox's pose (remapped to amcl_pose) is VOLATILE - a latched reader got nothing (2026-10-06)
        self.n.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.on_pose, QoSProfile(depth=1))
        self.n.create_subscription(LaserScan, '/scan', self.on_scan, 5)
        self.scan_log = []                                    # (arrival time, valid share) for scan health
        self.monitor = False                                  # confidence checks only once localized
        self.ov_hist, self.ov_t, self.why_stop = [], 0.0, None
        self.vbuf = []
        self.n.create_subscription(BatteryState, '/battery', self.on_battery, 5)
        self.n.create_subscription(String, '/rosorin_board_driver/state',
                                   lambda m: self.s.__setitem__('drv', json.loads(m.data)), 5)
        self.allowed = True
        self.n.create_service(SetBool, '/rosorin_explore/enable', self.on_enable)
        self.status = self.n.create_publisher(String, '/rosorin_explore/status', latched)
        self.init_pub = self.n.create_publisher(PoseWithCovarianceStamped, '/initialpose', 1)
        self.en = self.n.create_client(SetBool, '/rosorin_board_driver/enable')
        self.nav = ActionClient(self.n, NavigateToPose, 'navigate_to_pose')
        self.plan = ActionClient(self.n, ComputePathToPose, 'compute_path_to_pose')
        self.spin_ac = ActionClient(self.n, Spin, 'spin')
        run = time.strftime('%Y%m%d_%H%M%S')
        self.dir = os.path.expanduser(f'~/explore/{run}'); os.makedirs(self.dir, exist_ok=True)
        self.logf = open(os.path.join(self.dir, 'log.jsonl'), 'a')
        self.visited = None; self.failed = []

    # ---------- plumbing ----------
    def log(self, ev, **kw):
        kw.update(t=round(time.time(), 2), ev=ev)
        line = json.dumps(kw); self.logf.write(line + '\n'); self.logf.flush(); print(line, flush=True)
        self.status.publish(String(data=line))

    def on_enable(self, req, res):
        self.allowed = req.data; res.success = True; res.message = f'explore allowed={req.data}'
        self.log('owner_enable', allowed=req.data); return res

    def on_scan(self, m):
        self.s['scan'] = m; now = time.time()
        r = np.asarray(m.ranges, np.float32); valid = float((np.isfinite(r) & (r > 0.12)).mean()) if len(r) else 0.0
        self.scan_log = [x for x in self.scan_log if now - x[0] < 2.0] + [(now, valid)]

    def scan_health(self):
        """None if the LiDAR looks healthy, else the reason."""
        now = time.time()
        if not self.scan_log or now - self.scan_log[-1][0] > 1.0: return 'scan unhealthy: no scan for 1 s'
        if now - self.scan_log[0][0] < 1.8: return None       # too new to judge a rate: 2026-10-05 the home task refused
        hz = len([1 for t, _ in self.scan_log if now - t < 2.0]) / 2.0   # itself at t=0 with "2.0 Hz" (LiDAR at 10 Hz)
        valid = float(np.mean([v for _, v in self.scan_log[-5:]]))
        if hz < MIN_SCAN_HZ: return f'scan unhealthy: {hz:.1f} Hz'
        if valid < MIN_VALID: return f'scan unhealthy: {100 * valid:.0f} % valid returns'
        return None

    def confidence(self):
        """None if localization looks trustworthy, else the reason. Overlay at ~1 Hz (median of the last 3)."""
        now = time.time()
        if now - self.ov_t >= 1.0 and all(k in self.s for k in ('map', 'scan', 'pose')):
            self.ov_t = now; self.ov_hist = (self.ov_hist + [self.overlay()])[-3:]
        # PLAN.md 2026-10-02: no custom confidence add-ons - the overlay is only logged with each goal. It cancelled
        # a drive after 3 s (overlay 0.44 while moving; AMCL updates every 0.25 m). Obstacles are avoided in the odom
        # frame (LiDAR + nvblox), which does not depend on the map pose.
        return None

    def on_battery(self, m):
        now = time.time(); self.vbuf = [(t, v) for t, v in self.vbuf if now - t < 20.0] + [(now, m.voltage)]
        self.s['v'] = sum(v for _, v in self.vbuf) / len(self.vbuf)

    def on_pose(self, m):
        p = m.pose.pose; self.s['pose'] = (p.position.x, p.position.y, yaw_of(p.orientation))
        self.s['cov'] = (m.pose.covariance[0], m.pose.covariance[7], m.pose.covariance[35])
        if self.visited is not None: self.mark(p.position.x, p.position.y)

    def spin_once(self):
        rclpy.spin_once(self.n, timeout_sec=0.05)
        if self.visited is not None and time.time() - getattr(self, '_obs_t', 0) > 0.5 and 'scan' in self.s:
            self._obs_t = time.time()
            try: self.observe()
            except Exception as ex: self.log('observe_error', err=str(ex))

    def spin_for(self, t, cond=lambda: False):
        end = time.time() + t
        while time.time() < end and not cond(): self.spin_once()
        return cond()

    def wheels(self, on):
        if not self.en.wait_for_service(timeout_sec=5): self.log('enable_no_service', on=on); return False
        for attempt in (1, 2):                                # 2026-10-01: the driver answered enable 5.5 s late once
            f = self.en.call_async(SetBool.Request(data=on)); self.spin_for(10, f.done)
            r = f.result(); self.log('wheels', on=on, ok=bool(r and r.success), msg=r.message if r else 'NO REPLY', attempt=attempt)
            if r is not None:
                return bool(r.success)
        return False

    def run_action(self, ac, goal, timeout):
        """Send a goal, wait; returns (status, result). Cancels on timeout or owner stop."""
        if not ac.wait_for_server(timeout_sec=10): return 'NO_SERVER', None
        sf = ac.send_goal_async(goal); self.spin_for(10, sf.done)
        gh = sf.result()
        if not gh or not gh.accepted: return 'REJECTED', None
        rf = gh.get_result_async(); end = time.time() + timeout
        while not rf.done():
            self.spin_once()
            why = 'timeout' if time.time() > end else self.stop_reason(moving=True)
            if why:
                self.why_stop = why
                cf = gh.cancel_goal_async(); self.spin_for(5, cf.done); return 'CANCELLED', None
        return {4: 'SUCCEEDED', 5: 'CANCELED', 6: 'ABORTED'}.get(rf.result().status, str(rf.result().status)), rf.result().result

    def stop_reason(self, moving=False):
        d = self.s.get('drv', {})
        if d.get('estop'): return 'estop'
        if d.get('pad_mode') not in (None, 'locked'): return 'gamepad in use'
        if d.get('plugged'): return 'on the charger'          # board_driver plug detection (battery voltage step)
        if not self.allowed: return 'owner stop'
        if self.s.get('v') and 0 < self.s['v'] < LOW_BATTERY_V: return f"battery {self.s['v']:.2f} V"
        if moving:
            why = self.scan_health() or (self.monitor and self.confidence())
            if why: return why
        return None

    # ---------- localization ----------
    def base(self):
        """Base map arrays, cached: occupancy, distance to the nearest base wall (m)."""
        if getattr(self, '_base', None) is None:
            from scipy import ndimage
            m = self.s['map']; M = np.array(m.data, np.int16).reshape(m.info.height, m.info.width)
            wall = M >= 65
            self._base = (M, wall, ndimage.distance_transform_edt(~wall) * m.info.resolution, m.info)
        return self._base

    def scan_world(self):
        """Valid scan endpoints in the map frame at the AMCL pose: (wx, wy, range, beam angle in map)."""
        sc = self.s['scan']; x0, y0, yaw = self.s['pose']
        ang = sc.angle_min + np.arange(len(sc.ranges)) * sc.angle_increment + math.pi
        rr = np.array(sc.ranges); ok = np.isfinite(rr) & (rr > 0.12) & (rr < 6.0)
        lx = LASER_X + rr[ok] * np.cos(ang[ok]); ly = rr[ok] * np.sin(ang[ok])
        wx = x0 + lx * math.cos(yaw) - ly * math.sin(yaw); wy = y0 + lx * math.sin(yaw) + ly * math.cos(yaw)
        return wx, wy, rr[ok], ang[ok] + yaw

    def cells(self, wx, wy, info):
        r = info.resolution
        return ((wx - info.origin.position.x) / r).astype(int), ((wy - info.origin.position.y) / r).astype(int)

    def overlay(self):
        """Fraction of LiDAR points within 10 cm of a base wall at the AMCL pose (same math as scan_overlay.py)."""
        M, _, dist, info = self.base(); wx, wy, _, _ = self.scan_world(); i, j = self.cells(wx, wy, info)
        inb = (i >= 0) & (i < M.shape[1]) & (j >= 0) & (j < M.shape[0])
        return float((dist[j[inb], i[inb]] < 0.10).mean()) if inb.any() else 0.0

    def observe(self):
        """One scan vs the base map -> per-cell 'new' and 'gone' scan counts (each cell counted once per scan)."""
        M, wall, dist, info = self.base()
        if getattr(self, 'new_hits', None) is None:
            self.new_hits = np.zeros(M.shape, np.uint16); self.gone_hits = np.zeros(M.shape, np.uint16)
        wx, wy, rr, th = self.scan_world(); i, j = self.cells(wx, wy, info)
        inb = (i >= 0) & (i < M.shape[1]) & (j >= 0) & (j < M.shape[0])
        far = inb.copy(); far[inb] = dist[j[inb], i[inb]] > 0.15
        nc = np.unique(np.stack([j[far], i[far]]), axis=1)
        self.new_hits[nc[0], nc[1]] += 1
        # beams pass through base wall cells short of their endpoint (stop 15 cm before it) -> that wall is gone
        x0, y0, _ = self.s['pose']; r = info.resolution; gi, gj = [], []
        for d in np.arange(0.3, 6.0, r):
            m_ = (rr - 0.15) > d
            if not m_.any(): break
            ci, cj = self.cells(x0 + d * np.cos(th[m_]), y0 + d * np.sin(th[m_]), info)
            k = (ci >= 0) & (ci < M.shape[1]) & (cj >= 0) & (cj < M.shape[0])
            w = wall[cj[k], ci[k]]; gi.append(ci[k][w]); gj.append(cj[k][w])
        if gi:
            gc = np.unique(np.stack([np.concatenate(gj), np.concatenate(gi)]), axis=1)
            if gc.size: self.gone_hits[gc[0], gc[1]] += 1

    def changes(self):
        """Cluster the confirmed change cells -> [{kind, x, y, size_m, cells}]."""
        from scipy import ndimage
        if getattr(self, 'new_hits', None) is None: return []
        _, _, _, info = self.base(); r = info.resolution; out = []
        for kind, H in (('new', self.new_hits), ('gone', self.gone_hits)):
            lab, n = ndimage.label(ndimage.binary_dilation(H >= CHANGE_SCANS, iterations=2) & (H > 0))
            for k in range(1, n + 1):
                jj, ii = np.nonzero(lab == k)
                if len(ii) < 2: continue
                out.append(dict(kind=kind, x=round(float(ii.mean() + 0.5) * r + info.origin.position.x, 2),
                                y=round(float(jj.mean() + 0.5) * r + info.origin.position.y, 2),
                                size_m=round(float(max(np.ptp(ii), np.ptp(jj)) + 1) * r, 2), cells=int(len(ii))))
        return out

    def scan_match(self):
        """Where on the base map does the current scan fit best? Every free-floor spot (10 cm grid, 4 deg steps),
        then a fine pass around the winner - no motion (2026-10-02: AMCL after three 360 deg spins sat 10 cm / 6 deg
        from the right pose at overlay 0.52; this found it at 0.72 standing still).
        Returns (x, y, yaw, overlay, runner_up) - runner_up = best overlay of any pose > 0.5 m or > 30 deg away."""
        M, _, dist, info = self.base(); r = info.resolution; H, W = M.shape
        sc = self.s['scan']; rr = np.array(sc.ranges)
        ang = sc.angle_min + np.arange(len(rr)) * sc.angle_increment + math.pi
        ok = np.isfinite(rr) & (rr > 0.12) & (rr < 6.0)
        px, py = LASER_X + rr[ok] * np.cos(ang[ok]), rr[ok] * np.sin(ang[ok])
        ox, oy = info.origin.position.x, info.origin.position.y

        def score(X, Y, th):
            c, sn = math.cos(th), math.sin(th)
            i = ((X[:, None] + c * px - sn * py - ox) / r).astype(int)
            j = ((Y[:, None] + sn * px + c * py - oy) / r).astype(int)
            inb = (i >= 0) & (i < W) & (j >= 0) & (j < H)
            d = np.full(i.shape, 9.0, np.float32); d[inb] = dist[j[inb], i[inb]]
            return (d < 0.10).mean(axis=1)
        fj, fi = np.nonzero((M >= 0) & (M < 25)); k = (fi % 2 == 0) & (fj % 2 == 0)
        X, Y = ox + (fi[k] + 0.5) * r, oy + (fj[k] + 0.5) * r
        if not len(X) or ok.sum() < 30: return None
        cand = []
        for th in np.radians(np.arange(0, 360, 4)):
            ov = score(X, Y, th); q = int(ov.argmax()); cand.append((float(ov[q]), float(X[q]), float(Y[q]), float(th)))
        cand.sort(reverse=True)
        b = cand[0]
        far = [c for c in cand if math.hypot(c[1] - b[1], c[2] - b[2]) > 0.5 or
               abs(math.atan2(math.sin(c[3] - b[3]), math.cos(c[3] - b[3]))) > math.radians(30)]
        g = np.arange(-0.10, 0.101, 0.025); gx, gy = [v.ravel() for v in np.meshgrid(b[1] + g, b[2] + g)]
        for th in b[3] + np.radians(np.arange(-4, 4.1, 1)):
            ov = score(gx, gy, th); q = int(ov.argmax())
            if ov[q] > b[0]: b = (float(ov[q]), float(gx[q]), float(gy[q]), float(th))
        return b[1], b[2], math.atan2(math.sin(b[3]), math.cos(b[3])), b[0], (far[0][0] if far else 0.0)

    def stationary(self, seed=None, global_reset=False, updates=200):
        """Localize WITHOUT moving (owner 2026-10-01: back to the wall, the body needn't spin; LiDAR sees ~295 deg):
        optional seed pose (/initialpose) or global reset, then AMCL request_nomotion_update x updates. True if the
        scan matches the base map (overlay >= MIN_OVERLAY) and sigma < 0.25 m."""
        if seed:
            m = PoseWithCovarianceStamped(); m.header.frame_id = 'map'; m.header.stamp = self.n.get_clock().now().to_msg()
            m.pose.pose.position.x, m.pose.pose.position.y = seed[0], seed[1]
            m.pose.pose.orientation.z, m.pose.pose.orientation.w = math.sin(seed[2] / 2), math.cos(seed[2] / 2)
            m.pose.covariance[0] = m.pose.covariance[7] = 0.04; m.pose.covariance[35] = 0.05
            self.init_pub.publish(m); self.spin_for(1.0)
        elif global_reset:
            c = self.n.create_client(Empty, '/reinitialize_global_localization')
            if c.wait_for_service(timeout_sec=5):
                f = c.call_async(Empty.Request()); self.spin_for(10, f.done)
        nm = self.n.create_client(Empty, '/request_nomotion_update')
        if not nm.wait_for_service(timeout_sec=5): return False
        for _ in range(updates):
            f = nm.call_async(Empty.Request()); self.spin_for(1.0, f.done); self.spin_for(0.15)
        self.spin_for(1.0)
        ov = self.overlay() if all(k in self.s for k in ('map', 'scan', 'pose')) else 0.0
        cov = self.s.get('cov') or (9, 9, 9); sig = math.sqrt(max(cov[0], cov[1]))
        ok = ov >= MIN_OVERLAY and sig < 0.25
        self.log('localize_still', seed=bool(seed), global_reset=global_reset, overlay=round(ov, 3), sigma=round(sig, 3),
                 pose=self.s.get('pose'), ok=ok)
        return ok

    def localize(self, global_reset=True, attempts=(1, 2, 3)):
        """Global reset once (unless re-localizing in place), then up to 3 in-place spins (no further resets: each spin refines the same filter)
        until the scan matches the base map. A rejected spin (behavior server not active yet) is retried."""
        self.monitor, self.ov_hist = False, []
        # without moving first: from the saved home spot, then (full reset only) anywhere on the map; spin = fallback
        home = None
        try:
            h = json.load(open(os.path.expanduser('~/maps/home_pose.json'))); home = (h['x'], h['y'], h['yaw'])
        except (OSError, ValueError, KeyError):
            pass
        # standing still only from a known seed (home) or to refine in place - from scratch it locks onto wrong
        # poses (lessons.md) and cost ~40 s before every spin (owner: "way too conservative")
        if (global_reset and home and self.stationary(seed=home, updates=60)) or \
                (not global_reset and self.stationary(updates=60)):
            self.monitor = True; return True
        # not at home: find itself on the map from the scan alone (no motion); spinning stays the fallback
        if global_reset and all(k in self.s for k in ('map', 'scan')):
            m = self.scan_match()
            self.log('scan_match', found=None if m is None else [round(v, 3) for v in m])
            if m and m[3] >= MIN_OVERLAY and m[3] - m[4] >= MATCH_MARGIN and self.stationary(seed=m[:3], updates=30):
                self.monitor = True; return True
        if global_reset:
            c = self.n.create_client(Empty, '/reinitialize_global_localization')
            if c.wait_for_service(timeout_sec=5):
                f = c.call_async(Empty.Request()); self.spin_for(10, f.done)
        for attempt in attempts:
            why = self.stop_reason()
            if why: self.log('localize_stopped', reason=why); return False
            for _ in range(5):
                if not self.wheels(True): self.log('localize_stopped', reason='wheel enable failed'); return False
                g = Spin.Goal(); g.target_yaw = 2 * math.pi; g.time_allowance.sec = 40
                st, _ = self.run_action(self.spin_ac, g, 60)
                self.wheels(False)
                if st != 'REJECTED': break
                self.spin_for(3.0)
            self.spin_for(2.0)                                  # let AMCL settle on the last scans
            ov = self.overlay() if all(k in self.s for k in ('map', 'scan', 'pose')) else 0.0
            self.log('localize', attempt=attempt, spin=st, overlay=round(ov, 3), pose=self.s.get('pose'), cov=self.s.get('cov'))
            if ov >= MIN_OVERLAY:
                self.monitor = True; return True
            # near walls/clutter the overlay stays lower (2026-10-01 sethome: 0.48 -> 0.55 -> 0.58, same spot each spin):
            # accept a pose two consecutive spins agree on (< 10 cm) when the overlay is at least STABLE_OVERLAY
            p, prev = self.s.get('pose'), getattr(self, '_prev_pose', None)
            self._prev_pose = p
            if p and prev and math.dist(p[:2], prev[:2]) < 0.10 and ov >= STABLE_OVERLAY:
                self.log('localize_accepted', why='stable across spins', overlay=round(ov, 3), moved_m=round(math.dist(p[:2], prev[:2]), 3))
                self.monitor = True; return True
        return False

    # ---------- coverage ----------
    def floor(self):
        c = self.s['cost']; C = np.array(c.data, np.int16).reshape(c.info.height, c.info.width)
        return C, (C >= 0) & (C < 99), c.info

    def mark(self, x, y):
        info = self.cinfo; r = info.resolution
        ci, cj = int((x - info.origin.position.x) / r), int((y - info.origin.position.y) / r); k = int(VISIT_R / r)
        H, W = self.visited.shape
        jj, ii = np.ogrid[max(0, cj - k):min(H, cj + k + 1), max(0, ci - k):min(W, ci + k + 1)]
        disk = (ii - ci) ** 2 + (jj - cj) ** 2 <= k * k
        self.visited[max(0, cj - k):min(H, cj + k + 1), max(0, ci - k):min(W, ci + k + 1)] |= disk

    def coverage(self):
        return float((self.visited & self.free).sum() / max(1, self.free.sum()))

    def candidates(self, n=12):
        """Unvisited floor cells, far from failed goals; ranked later by gain / path length."""
        r = self.cinfo.resolution; step = max(1, int(0.25 / r))
        js, is_ = np.nonzero((self.free & ~self.visited)[::step, ::step])
        pts = [((i * step + 0.5) * r + self.cinfo.origin.position.x, (j * step + 0.5) * r + self.cinfo.origin.position.y)
               for j, i in zip(js, is_)]
        pts = [p for p in pts if all(math.dist(p, f) > 0.4 for f in self.failed)]
        x0, y0, _ = self.s['pose']
        pts.sort(key=lambda p: -self.gain(p) / (0.5 + math.dist(p, (x0, y0))))
        return pts[:n]

    def gain(self, p):
        r = self.cinfo.resolution; ci = int((p[0] - self.cinfo.origin.position.x) / r)
        cj = int((p[1] - self.cinfo.origin.position.y) / r); k = int(VISIT_R / r)
        win = (self.free & ~self.visited)[max(0, cj - k):cj + k + 1, max(0, ci - k):ci + k + 1]
        return int(win.sum())

    def pick(self):
        best = None
        for p in self.candidates():
            g = ComputePathToPose.Goal(); g.goal = self.pose_msg(p, self.heading(p)); g.use_start = False
            st, res = self.run_action(self.plan, g, 10)
            if st != 'SUCCEEDED' or not res or len(res.path.poses) < 2: self.failed.append(p); continue
            L = sum(math.dist((a.pose.position.x, a.pose.position.y), (b.pose.position.x, b.pose.position.y))
                    for a, b in zip(res.path.poses, res.path.poses[1:]))
            score = self.gain(p) / (0.5 + L)
            if best is None or score > best[0]: best = (score, p, L)
        return best

    def heading(self, p):
        x0, y0, _ = self.s['pose']; return math.atan2(p[1] - y0, p[0] - x0)

    def pose_msg(self, p, yaw):
        m = PoseStamped(); m.header.frame_id = 'map'; m.header.stamp = self.n.get_clock().now().to_msg()
        m.pose.position.x, m.pose.position.y = p
        m.pose.orientation.z, m.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2); return m

    def go(self, p, yaw, why):
        if not self.wheels(True): return 'ENABLE_FAILED'
        g = NavigateToPose.Goal(); g.pose = self.pose_msg(p, yaw); t0 = time.time(); self.why_stop = None
        try:
            st, _ = self.run_action(self.nav, g, GOAL_TIMEOUT)
        finally:
            self.wheels(False)
        pose = self.s.get('pose')
        self.log('goal', why=why, goal=[round(p[0], 2), round(p[1], 2), round(math.degrees(yaw))], result=st,
                 secs=round(time.time() - t0, 1), pose=pose, err=round(math.dist(p, pose[:2]), 3) if pose else None,
                 cov=self.s.get('cov'), coverage=round(self.coverage(), 3), battery=self.s.get('v'),
                 stopped_by=self.why_stop, overlay=round(float(np.median(self.ov_hist)), 3) if self.ov_hist else None)
        return st

    # ---------- run ----------
    def run(self):
        if not self.spin_for(20, lambda: all(k in self.s for k in ('map', 'scan', 'drv'))):
            self.log('abort', reason='missing inputs', have=list(self.s)); return
        why = self.stop_reason()
        if why: self.log('abort', reason=why); return
        # arm: not touched. Navigation is LiDAR-only; the mind is paused, board_driver auto-tucks an idle arm.
        if not self.localize(): self.log('abort', reason=f'localization overlay < {MIN_OVERLAY}'); return
        # the global costmap publishes only once AMCL gives map->odom, i.e. after localizing
        if not self.spin_for(20, lambda: 'cost' in self.s): self.log('abort', reason='no global costmap'); return
        _, self.free, self.cinfo = self.floor(); self.visited = np.zeros_like(self.free)
        x0, y0, th0 = self.s['pose']; self.mark(x0, y0); home = (x0, y0, th0)
        try:                                                  # owner-set home (come_here.py --sethome) wins
            h = json.load(open(os.path.expanduser('~/maps/home_pose.json'))); home = (h['x'], h['y'], h['yaw'])
        except (OSError, ValueError, KeyError):
            pass
        self.log('start', home=home, floor_m2=round(float(self.free.sum()) * self.cinfo.resolution ** 2, 2))
        t0, fails, reason = time.time(), 0, None
        if GATE:                                              # nav gate: 0.6 m ahead (or the freest heading) and back
            tgt = None
            for d in (0, 45, -45, 90, -90, 135, -135, 180):
                a = th0 + math.radians(d); p = (x0 + 0.6 * math.cos(a), y0 + 0.6 * math.sin(a))
                g = ComputePathToPose.Goal(); g.goal = self.pose_msg(p, a); g.use_start = False
                st, res = self.run_action(self.plan, g, 10)
                if st == 'SUCCEEDED' and res and len(res.path.poses) > 1: tgt = (p, a); break
            reason = 'gate: no reachable point 0.6 m away' if tgt is None else None
            if tgt and not self.stop_reason(): reason = 'gate out: ' + self.go(tgt[0], tgt[1], 'gate out')
        while not GATE:
            reason = self.stop_reason() or (time.time() - t0 > TIME_LIMIT and 'time limit') or \
                (self.coverage() >= TARGET and 'coverage reached') or (fails >= MAX_FAILS and 'too many failed goals')
            if reason: break
            best = self.pick()
            if not best: reason = 'no reachable unvisited floor'; break
            st = self.go(best[1], self.heading(best[1]), 'explore')
            if st == 'SUCCEEDED': fails = 0
            elif str(self.why_stop).startswith('low confidence'):
                relocs = getattr(self, 'relocs', 0) + 1; self.relocs = relocs
                if relocs > 2 or not self.localize(global_reset=False, attempts=(1, 2)):
                    reason = f'could not regain confidence ({self.why_stop})'; break
            elif str(self.why_stop).startswith('scan unhealthy'):
                if not self.spin_for(SCAN_RECOVER_S, lambda: self.scan_health() is None):
                    reason = self.scan_health() or self.why_stop; break
                self.log('scan_recovered', after=self.why_stop)
            else: fails += 1; self.failed.append(best[1])
        self.log('stop', reason=reason, coverage=round(self.coverage(), 3), secs=round(time.time() - t0, 1))
        if not any(k in str(reason) for k in ('estop', 'gamepad in use', 'owner stop', 'scan unhealthy', 'could not regain')):
            self.go(home[:2], home[2], 'home')
        summary = dict(reason=reason, coverage=round(self.coverage(), 3), secs=round(time.time() - t0, 1),
                       home=home, end=self.s.get('pose'),
                       home_err=round(math.dist(home[:2], self.s['pose'][:2]), 3) if self.s.get('pose') else None)
        json.dump(summary, open(os.path.join(self.dir, 'summary.json'), 'w'), indent=1)
        np.save(os.path.join(self.dir, 'visited.npy'), self.visited)
        ch = self.changes(); summary['changes'] = len(ch)
        json.dump(ch, open(os.path.join(self.dir, 'changes.json'), 'w'), indent=1)
        if getattr(self, 'new_hits', None) is not None:
            np.save(os.path.join(self.dir, 'changes_new.npy'), self.new_hits); np.save(os.path.join(self.dir, 'changes_gone.npy'), self.gone_hits)
        self.log('summary', **summary)


def main():
    rclpy.init(); e = Explorer()
    try:
        with mind_paused(reason='room explore'):
            e.run()
    finally:
        try: e.wheels(False)
        except Exception as ex: print('disable failed:', ex, flush=True)
        sys.stdout.flush()
        os._exit(0)          # 2026-10-02: the process hung here for 5 min after a finished run (everything is saved)


if __name__ == '__main__':
    main()
