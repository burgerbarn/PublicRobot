"""'Come check this out' (owner 2026-10-01): the robot drives itself to the owner and stops ~0.7 m away, facing them.
Started by Buddy (robot_api POST /come -> rosorin-come.service); Claude does not drive.

1. Where is the owner: the mind is tracking them, so the camera points at them - camera yaw in base_link (TF
   base_link -> camera_link0) = bearing; LiDAR points within +-6 deg of that bearing (0.25-4 m) -> distance (their
   legs/body; 20th percentile). Bearing is kept in the odom frame (EKF yaw) so the localization spin doesn't lose it.
2. Localize (same as the explorer: global reset + spin, overlay >= 0.65) - unless AMCL is already confident.
3. Nav2 goal: STOP_AT m short of the owner along the bearing, facing them. Keepout, approach collision model,
   confidence + LiDAR health monitors and wheel enable/disable all come from room_explorer.Explorer.
4. Exit: wheels off, mind resumes (finds the owner again); Buddy then runs robot_see.
Modes (rosorin-task@<mode>.service): --come (default), --home (drive to the saved home pose and face its heading),
--sethome (localize - standing still first - then save the pose as home: ~/maps/home_pose.json), --dry (owner fix
only), --locate (localize standing still only; never moves).
Status: /rosorin_explore/status (JSON lines) + ~/explore/<run>/log.jsonl; stop = /rosorin_explore/enable false."""
import json, math, os, sys, time

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import room_explorer as R  # noqa: E402
from mind_pause import mind_paused  # noqa: E402
from unstick import Unstick  # noqa: E402
from nav2_msgs.srv import ClearEntireCostmap  # noqa: E402
HOME_TRIES, HOME_BUDGET = 4, 360.0   # 2026-10-06 ("he quits too easy"): tries to get home, with an escape or fresh costmaps between

STOP_AT = 0.7            # m from the owner
HOME = os.path.expanduser('~/maps/home_pose.json')   # Buddy's/the robot's default spot (owner 2026-10-01)


def load_home():
    try:
        h = json.load(open(HOME)); return (h['x'], h['y'], h['yaw'])
    except (OSError, ValueError, KeyError):
        return None
MIN_MOVE = 0.15          # closer than this to the stop point: just turn to face them


def yaw_q(q): return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class Come(R.Explorer):
    def __init__(self):
        super().__init__()
        self.tf = Buffer(); TransformListener(self.tf, self.n)
        self.n.create_subscription(Odometry, '/odometry/filtered', lambda m: self.s.__setitem__('odom_yaw', yaw_q(m.pose.pose.orientation)), 10)
        self.navst = {}                                       # the navigation supervisor's word on localization
        self.n.create_subscription(R.String, '/nav/status', lambda m: self.navst.update(json.loads(m.data)),
                                   R.QoSProfile(depth=1, durability=R.DurabilityPolicy.TRANSIENT_LOCAL))

    def pose_tf(self):
        """(x, y, yaw_deg) in the map frame from TF: always there once localized, unlike the pose topic (slam_toolbox
        publishes it only as it processes scans)."""
        try:
            t = self.tf.lookup_transform('map', 'base_link', Time()).transform
            return (t.translation.x, t.translation.y, math.degrees(yaw_q(t.rotation)))
        except Exception:
            return None

    def coverage(self):
        return 0.0

    def owner_fix(self):
        """(bearing in odom frame, distance) of the owner, or (None, reason)."""
        try:
            t = self.tf.lookup_transform('base_link', 'camera_link0', rclpy.time.Time())
        except Exception as e:
            return None, f'no camera TF: {e}'
        b = yaw_q(t.transform.rotation)
        sc = self.s['scan']; r = np.asarray(sc.ranges, np.float32)
        ang = sc.angle_min + np.arange(len(r)) * sc.angle_increment + math.pi
        ok = np.isfinite(r) & (r > 0.12)
        lx = R.LASER_X + r[ok] * np.cos(ang[ok]); ly = r[ok] * np.sin(ang[ok])
        bear = np.arctan2(ly, lx); dist = np.hypot(lx, ly)
        sel = (np.abs(np.angle(np.exp(1j * (bear - b)))) < math.radians(6)) & (dist > 0.25) & (dist < 4.0)
        if sel.sum() < 2:
            return None, f'no LiDAR return toward the owner (bearing {math.degrees(b):.0f} deg)'
        d = float(np.percentile(dist[sel], 20))
        return (b + self.s['odom_yaw'], d), None

    def localized(self):
        # 2026-10-06: with slam_toolbox localizing, the supervisor (nav_supervisor.py) already knows whether the
        # robot is localized (its scan fit against the kept map); the AMCL-era reset/spin dance below is the fallback
        if self.navst.get('ready') and time.time() - self.navst.get('t', 0) < 30 and self.pose_tf():
            p = self.pose_tf(); self.s.setdefault('pose', (p[0], p[1], math.radians(p[2])))
            self.log('localized', by='nav_supervisor', fit=self.navst.get('fit'), pose=[round(v, 2) for v in p])
            self.monitor = True; return True
        cov = self.s.get('cov')
        warm = cov and math.sqrt(max(cov[0], cov[1])) < 0.2 and 'pose' in self.s and self.overlay() >= R.MIN_OVERLAY
        if warm or self.localize():
            self.monitor = True; return True
        self.log('abort', reason=f'localization overlay < {R.MIN_OVERLAY}'); return False

    def run(self):
        if '--locate' in sys.argv:                            # where am I, standing still only (never moves)
            if not self.spin_for(20, lambda: all(k in self.s for k in ('map', 'scan', 'drv'))):
                self.log('abort', reason='missing inputs', have=list(self.s)); return
            h = load_home()
            ok = (h is not None and self.stationary(seed=h)) or self.stationary(global_reset=True)
            self.log('summary', result='SUCCEEDED' if ok else 'FAILED', pose=self.s.get('pose')); return
        if '--sethome' in sys.argv or '--home' in sys.argv:
            if not self.spin_for(20, lambda: all(k in self.s for k in ('map', 'scan', 'drv'))):
                self.log('abort', reason='missing inputs', have=list(self.s)); return
            # 2026-10-09 (simulated rebuild): the three inputs can arrive before the latched /nav/status and the TF
            # buffer are read (mind_paused() blocks without spinning this node); localized() then took the spin fallback
            # (--sethome only: tested without motion in the simulation; --home drives and was not run with this wait)
            if '--sethome' in sys.argv:
                self.spin_for(10, lambda: self.navst.get('ready') and self.pose_tf())
            why = self.stop_reason()
            if why: self.log('abort', reason=why); return
            if '--sethome' in sys.argv:
                if not self.localized(): return
                x, y, yaw = self.s['pose']
                json.dump({'x': x, 'y': y, 'yaw': yaw, 'overlay': round(self.overlay(), 3), 'set': time.time()},
                          open(HOME, 'w'), indent=1)
                self.log('summary', result='SUCCEEDED', home=[round(x, 3), round(y, 3), round(math.degrees(yaw), 1)])
                return
            h = load_home()
            if not h: self.log('abort', reason='no home saved yet'); return
            if not self.localized(): return
            un = Unstick(self.n, self.tf); end = time.time() + HOME_BUDGET
            clear = [self.n.create_client(ClearEntireCostmap, f'/{c}_costmap/clear_entirely_{c}_costmap') for c in ('global', 'local')]
            for i in range(HOME_TRIES):
                st = self.go(h[:2], h[2], 'go home')
                if st == 'SUCCEEDED' or self.stop_reason() or time.time() > end or i == HOME_TRIES - 1:
                    break
                p, c = self.pose_tf(), self.s.get('cost')
                if p and c is not None and un.boxed_in(c, p[0], p[1]) and self.wheels(True):
                    try:                                      # planning from inside an obstacle's ring: crawl out first
                        un.escape(lambda: self.s.get('cost'), self.pose_tf, self.stop_reason, self.spin_for, self.log)
                    finally:
                        self.wheels(False)
                else:
                    for cl in clear:
                        if cl.wait_for_service(timeout_sec=3):
                            f = cl.call_async(ClearEntireCostmap.Request()); self.spin_for(5, f.done)
                    self.log('costmaps_cleared')
                self.spin_for(3.0)
            self.log('summary', result=st, stopped_by=self.why_stop, tries=i + 1); return
        need = ('scan', 'drv', 'odom_yaw') + (() if '--dry' in sys.argv else ('map',))
        if not self.spin_for(20, lambda: all(k in self.s for k in need)):
            self.log('abort', reason='missing inputs', have=list(self.s)); return
        why = self.stop_reason()
        if why: self.log('abort', reason=why); return
        self.spin_for(1.0)                                    # TF buffer
        fix, err = self.owner_fix()
        if not fix: self.log('abort', reason=err); return
        ob, od = fix
        self.log('owner', bearing_deg=round(math.degrees(ob - self.s['odom_yaw'])), dist_m=round(od, 2))
        if '--dry' in sys.argv:                               # verify the owner fix only: no localization, no wheels
            self.log('summary', result='DRY', owner_dist_m=round(od, 2)); return
        if not self.localized(): return
        x0, y0, th = self.s['pose']
        b = ob - self.s['odom_yaw']                           # owner bearing relative to the robot now (after spin)
        a = th + b; go = max(0.0, od - STOP_AT)
        if go < MIN_MOVE:
            st = self.go((x0, y0), a, 'face owner')
        else:
            st = self.go((x0 + go * math.cos(a), y0 + go * math.sin(a)), a, 'come to owner')
        self.log('summary', result=st, stopped_by=self.why_stop, owner_dist_m=round(od, 2), moved_m=round(go, 2))


def main():
    rclpy.init(); e = Come()
    try:
        with mind_paused(reason='come check this out'):
            e.run()
    finally:
        try: e.wheels(False)
        except Exception as ex: print('disable failed:', ex, flush=True)
        e.n.destroy_node(); rclpy.try_shutdown()


if __name__ == '__main__':
    main()
