"""Boxed in: the robot's own way out when Nav2 cannot plan from where it stands.
2026-10-06: it drove along a bean bag until the bag was 16 cm from its LiDAR; its own cell sat inside the bag's
inflation ring (NavFn treats inscribed cost as an obstacle), eight goals aborted, the home goal aborted, and it gave
up 1.8 m from home. Owner: "he quits too easy". Nav2's BackUp/Spin recoveries are out (blind rear, 2026-10-02), so:
  boxed_in(cost, x, y)  the published global costmap at the robot's cell is inscribed or lethal (>= 99)
  escape(...)           the freest SENSED direction - free costmap cells within 1 m in the robot's frame, LiDAR clear
                        in that direction, never reverse (vx >= 0), never the blind rear - then a slow crawl
                        (0.08 m/s on cmd_vel_nav: velocity_smoother -> collision_monitor -> cmd_vel, so the monitor
                        still gates it and the contact reflex still watches) until the cell is clear plus a margin,
                        or 0.5 m, or 10 s, or a stop reason. Wheels must already be enabled.
Used by explore_auto.py (between goals, before giving up, before "I could not get home") and come_here.py --home."""
import math, time

import numpy as np
from geometry_msgs.msg import Twist
from rclpy.time import Time
from sensor_msgs.msg import LaserScan

BLOCKED = 99               # published grid: 99 = inscribed, 100 = lethal
SPEED = 0.08               # m/s: the strafe limit; forward at the same crawl
HEADINGS = (0, 30, -30, 60, -60, 90, -90)   # deg in the robot's frame; |h| <= 90 keeps vx >= 0 (no reverse)
CORRIDOR_DEG, LOOK_M = 20.0, 1.0             # free cells counted within +-CORRIDOR of a heading, up to LOOK_M
MIN_FREE = 40              # free 5 cm cells needed in the corridor (~0.1 m2) to call a heading a way out
LIDAR_CLEAR, LIDAR_STOP = 0.35, 0.22         # m: needed in the travel direction to start / to keep going
MAX_M, MAX_S, MARGIN_M = 0.5, 10.0, 0.15


class Unstick:
    def __init__(self, node, tf_buffer):
        self.n, self.buf, self.scan = node, tf_buffer, None
        self.n.create_subscription(LaserScan, '/scan', lambda m: setattr(self, 'scan', m), 5)
        self.cmd = self.n.create_publisher(Twist, 'cmd_vel_nav', 10)

    @staticmethod
    def cell(cost, x, y):
        i = cost.info
        cx, cy = int((x - i.origin.position.x) / i.resolution), int((y - i.origin.position.y) / i.resolution)
        if not (0 <= cx < i.width and 0 <= cy < i.height):
            return None
        return int(cost.data[cy * i.width + cx])

    def boxed_in(self, cost, x, y):
        c = self.cell(cost, x, y) if cost is not None else None
        return c is not None and c >= BLOCKED

    def laser_yaw(self):
        """The LiDAR's yaw in base_link (it is mounted turned 180 deg: verified by TF 2026-10-06, not assumed)."""
        try:
            q = self.buf.lookup_transform('base_link', self.scan.header.frame_id, Time()).transform.rotation
            return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        except Exception:
            return None

    def lidar_min(self, heading_deg, half_deg=25.0):
        """Nearest valid return within +-half_deg of a robot-frame heading; None when the LiDAR is blind there."""
        s = self.scan
        if s is None:
            return None
        ly = self.laser_yaw()
        if ly is None:
            return None
        rr = np.asarray(s.ranges, np.float32)
        ok = np.isfinite(rr) & (rr > s.range_min)
        a = np.degrees(s.angle_min + np.arange(len(rr)) * s.angle_increment + ly)
        d = (a - heading_deg + 180) % 360 - 180
        sel = ok & (np.abs(d) <= half_deg)
        return float(rr[sel].min()) if sel.any() else None

    def direction(self, cost, x, y, yaw):
        """Best robot-frame heading (deg) and its free-cell count; (None, why) when no sensed way out exists."""
        i = cost.info; r = i.resolution
        C = np.array(cost.data, np.int16).reshape(i.height, i.width)
        n = int(LOOK_M / r)
        cx, cy = int((x - i.origin.position.x) / r), int((y - i.origin.position.y) / r)
        ys, xs = np.mgrid[max(0, cy - n):min(i.height, cy + n + 1), max(0, cx - n):min(i.width, cx + n + 1)]
        W = C[ys, xs]
        dx = (xs - cx) * r; dy = (ys - cy) * r
        fwd = dx * math.cos(yaw) + dy * math.sin(yaw); left = -dx * math.sin(yaw) + dy * math.cos(yaw)
        ang = np.degrees(np.arctan2(left, fwd)); dist = np.hypot(fwd, left)
        free = (W == 0) & (dist <= LOOK_M) & (dist > 0.1)
        best, why = None, 'no free floor within 1 m'
        for h in HEADINGS:
            d = (ang - h + 180) % 360 - 180
            cnt = int((free & (np.abs(d) <= CORRIDOR_DEG)).sum())
            if cnt < MIN_FREE:
                continue
            lm = self.lidar_min(h)
            if lm is None:
                why = f'LiDAR blind toward {h} deg'; continue
            if lm < LIDAR_CLEAR:
                why = f'LiDAR {lm:.2f} m toward {h} deg'; continue
            if best is None or cnt > best[1]:
                best = (h, cnt, lm)
        return best, why

    def escape(self, cost_fn, pose_fn, stop_fn, spin_fn, log):
        """Crawl out along the best heading. cost_fn() -> OccupancyGrid, pose_fn() -> (x, y, yaw_deg) in the map
        frame, stop_fn() -> reason or None, spin_fn(s) spins the node. Returns (cleared: bool, detail: str)."""
        cost, p = cost_fn(), pose_fn()
        if cost is None or p is None:
            return False, 'no costmap or pose'
        yaw = math.radians(p[2])
        best, why = self.direction(cost, p[0], p[1], yaw)
        if best is None:
            log('escape_none', why=why); return False, why
        h, cnt, lm = best
        tw = Twist(); tw.linear.x = SPEED * math.cos(math.radians(h)); tw.linear.y = SPEED * math.sin(math.radians(h))
        log('escape_start', heading_deg=h, free_cells=cnt, lidar_m=round(lm, 2), pose=p)
        x0, y0 = p[0], p[1]; t0 = time.time(); cleared_at = None; moved = 0.0; detail = 'time'
        while time.time() - t0 < MAX_S:
            why = stop_fn()
            if why:
                detail = f'stopped: {why}'; break
            self.cmd.publish(tw); spin_fn(0.1)
            p = pose_fn()
            if p is None:
                continue
            moved = math.hypot(p[0] - x0, p[1] - y0)
            lm = self.lidar_min(h)
            if lm is not None and lm < LIDAR_STOP:
                detail = f'LiDAR {lm:.2f} m ahead of the move'; break
            if moved >= MAX_M:
                detail = f'{MAX_M} m moved'; break
            c = cost_fn()
            if c is not None and not self.boxed_in(c, p[0], p[1]):
                cleared_at = moved if cleared_at is None else cleared_at
                if moved - cleared_at >= MARGIN_M:
                    detail = 'clear'; break
            else:
                cleared_at = None
        for _ in range(3):
            self.cmd.publish(Twist()); spin_fn(0.1)
        c, p = cost_fn(), pose_fn()
        ok = bool(p and c is not None and not self.boxed_in(c, p[0], p[1]))
        log('escape_end', moved_m=round(moved, 2), secs=round(time.time() - t0, 1), detail=detail, cleared=ok, pose=p)
        return ok, detail
