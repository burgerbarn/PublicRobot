"""Keeps the always-on navigation usable, and says so (rosorin-nav.service, nav.launch.py).

Skills (explore, practice, go home, come) are clients: they read ~/status (latched JSON on /nav/status) or run
`ros2 run rosorin_base nav_wait [seconds]` and send goals only when it says ready. Nothing here moves the robot.

Why this node exists (docs/research/always_on_navigation_reference.md, relocalization_reference.md, 2026-10-05):
- Nav2's lifecycle manager on Humble never retries a failed bringup and leaves the servers half-alive; the stock cure
  is RESET then STARTUP, the stock readiness test is is_active. This node makes those calls.
- The global costmap waits without limit for map -> base_link, so navigation is started only after the robot has a
  pose on the map (the navigation manager has autostart off).
- AMCL forgets its pose when the process ends, and NVIDIA's grid localizer gives a pose without a confidence. So the
  pose is: remembered on disk -> checked against the live scan -> searched for when the check fails (GPU localizer
  first, then every free cell of the map) -> checked again. Only a checked pose reaches /initialpose.
  Check = share of scan points within 10 cm of a wall of the map at that pose (2026-10-05: right pose 92-93 %).
States: starting, localizing, lost, starting_nav, ready, healing. Three failed heals in a row -> exit (systemd restarts
the whole navigation)."""
import glob
import json
import math
import os
import shutil
import sys
import threading
import time

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')      # see vslam_odom.py: idle OpenBLAS threads spin

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import ComputePathToPose, FollowPath
from nav2_msgs.srv import ManageLifecycleNodes
from rclpy.action import ActionClient
from nav_msgs.msg import OccupancyGrid
from rclpy.executors import ExternalShutdownException, MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from slam_toolbox.srv import SaveMap, SerializePoseGraph
from std_srvs.srv import Empty, SetBool, Trigger
from tf2_ros import Buffer, TransformListener

PROBE_EVERY = 30.0          # s: while ready, ask the planner for a path to the robot's own pose this often
LATCHED = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
NEAR = 0.10                                   # m: a scan point this close to a map wall counts as explained


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class ScanMatcher:
    """Scores poses of the robot on the map against one LiDAR scan (points given in base_link)."""

    def __init__(self, grid):
        i = grid.info
        self.res, self.ox, self.oy, self.w, self.h = i.resolution, i.origin.position.x, i.origin.position.y, i.width, i.height
        m = np.array(grid.data, np.int16).reshape(self.h, self.w)
        self.dist = cv2.distanceTransform((m < 65).astype(np.uint8), cv2.DIST_L2, 5) * self.res
        ys, xs = np.nonzero(m == 0)
        self.free = np.stack([self.ox + (xs + 0.5) * self.res, self.oy + (ys + 0.5) * self.res], 1)

    def fits(self, poses, pts):
        """poses (N,3) x,y,yaw; pts (M,2) -> share of points within NEAR of a wall, per pose."""
        out = np.empty(len(poses), np.float32)
        step = max(1, 400000 // max(len(pts), 1))
        for a in range(0, len(poses), step):
            p = poses[a:a + step]
            c, s = np.cos(p[:, 2])[:, None], np.sin(p[:, 2])[:, None]
            i = ((p[:, 0:1] + pts[None, :, 0] * c - pts[None, :, 1] * s - self.ox) / self.res).astype(np.int32)
            j = ((p[:, 1:2] + pts[None, :, 0] * s + pts[None, :, 1] * c - self.oy) / self.res).astype(np.int32)
            ok = (i >= 0) & (i < self.w) & (j >= 0) & (j < self.h)
            d = np.where(ok, self.dist[np.clip(j, 0, self.h - 1), np.clip(i, 0, self.w - 1)], 9.9)
            out[a:a + step] = (d < NEAR).mean(1)
        return out

    def through_walls(self, poses, pts, origin=(0.048, 0.0), slack=0.20):
        """Share of beams that pass through a wall of the map before they end, per pose (0 = none).
        The end-point fit alone cannot tell places apart when the scan is mostly one or two walls: measured
        2026-10-05 (scripts/tests/scan_fit_replay.py), a scan taken near a wall fitted 5 other places at 0.96."""
        if not hasattr(self, 'wall'):
            self.wall = self.dist < self.res * 0.5                       # occupied cells
        out = np.empty(len(poses), np.float32)
        o = np.asarray(origin, np.float32)
        v = pts - o[None, :]; rng = np.hypot(v[:, 0], v[:, 1]); u = v / np.maximum(rng, 1e-6)[:, None]
        steps = np.arange(0.10, float(rng.max()), self.res, dtype=np.float32)          # along each beam, base frame
        d = np.minimum(steps[None, :], np.maximum(rng[:, None] - slack, 0.0))            # stop short of the end point
        bx = o[0] + u[:, 0:1] * d; by = o[1] + u[:, 1:2] * d                              # (M, S)
        for k, p in enumerate(poses):
            c, sn = math.cos(p[2]), math.sin(p[2])
            i = ((p[0] + bx * c - by * sn - self.ox) / self.res).astype(np.int32)
            j = ((p[1] + bx * sn + by * c - self.oy) / self.res).astype(np.int32)
            ok = (i >= 0) & (i < self.w) & (j >= 0) & (j < self.h)
            hit = ok & self.wall[np.clip(j, 0, self.h - 1), np.clip(i, 0, self.w - 1)]
            out[k] = hit.any(1).mean()
        return out

    def refine(self, pose, pts, r=0.30, step=0.05, a=15.0, astep=3.0):
        """Best pose near `pose` (within r metres, a degrees) and its fit."""
        d = np.arange(-r, r + 1e-6, step); y = np.radians(np.arange(-a, a + 1e-6, astep))
        gx, gy, gyaw = np.meshgrid(pose[0] + d, pose[1] + d, pose[2] + y, indexing='ij')
        poses = np.stack([gx.ravel(), gy.ravel(), gyaw.ravel()], 1)
        f = self.fits(poses, pts)
        f -= 1e-4 * np.hypot(poses[:, 0] - pose[0], poses[:, 1] - pose[1])       # ties: stay closest to the seed
        k = int(f.argmax())
        return poses[k], float(self.fits(poses[k:k + 1], pts)[0])

    def search(self, pts):
        """Every free cell on a 10 cm grid x 6 deg, then the best places refined. -> (pose, fit, second-best fit elsewhere)."""
        key = np.unique(np.round(self.free / 0.10).astype(np.int32), axis=0) * 0.10
        sub = pts[::max(1, len(pts) // 120)]
        best = []
        for yaw in np.radians(np.arange(0, 360, 6)):
            poses = np.column_stack([key, np.full(len(key), yaw)])
            f = self.fits(poses, sub)
            for k in np.argsort(f)[-3:]:
                best.append((float(f[k]), poses[k]))
        best.sort(key=lambda b: -b[0])
        done = []
        for _, p in best[:25]:
            if any(math.hypot(p[0] - q[0], p[1] - q[1]) < 0.4 and abs(math.remainder(p[2] - q[2], math.tau)) < 0.5
                   for q, _ in done):
                continue
            done.append(self.refine(p, pts, r=0.10, step=0.05, a=6.0, astep=2.0))
            if len(done) == 6:
                break
        done.sort(key=lambda b: -b[1])
        if not done:
            return None, 0.0, 0.0
        return done[0][0], done[0][1], (done[1][1] if len(done) > 1 else 0.0)


class NavSupervisor(Node):
    def __init__(self):
        super().__init__('nav_supervisor')
        dp = self.declare_parameter
        self.accept = dp('accept_fit', 0.65).value          # a pose is believed at or above this fit ...
        self.margin = dp('search_margin', 0.10).value       # ... and only when it beats the best other place by this
        self.lost_fit = dp('lost_fit', 0.40).value          # below this for lost_checks checks while standing -> lost
        self.lost_checks = dp('lost_checks', 6).value
        self.pose_file = os.path.expanduser(dp('pose_file', '~/maps/last_pose.json').value)
        self.home_file = os.path.expanduser(dp('home_file', '~/maps/home_pose.json').value)
        # the map that updates itself as it drives (slam_toolbox continuing the kept graph); False = AMCL on a file
        self.slam = bool(dp('slam', True).value)
        self.loc_manager = bool(dp('loc_manager', True).value)       # a lifecycle manager for localization nodes exists
        self.map_dir = os.path.expanduser(dp('map_dir', '~/maps').value)
        self.mapping, self.save_t, self.wheels_were_on = None, time.time(), False   # None: mode not yet set by us
        self.plugged = True                                   # until the driver says otherwise: no mapping on the charger
        self.map = self.matcher = self.scan = self.amcl = self.loc_result = None
        self.map_key = None
        self.plan_client, self.follow_client, self.probe_t, self.probe_fails, self.wheels_on = None, None, 0.0, 0, False
        self.moving_t = 0.0
        self.state, self.info = 'starting', {}
        self.placed = False
        self.heals = self.nav_fails = self.low = self.lost_n = 0
        self.saved = None
        self.laser = None
        self.buf = Buffer(); self.tfl = TransformListener(self.buf, self)
        self.pub = self.create_publisher(String, 'nav/status', LATCHED)
        self.init_pub = self.create_publisher(PoseWithCovarianceStamped, 'initialpose', 1)
        self.create_subscription(OccupancyGrid, 'map', self.on_map, LATCHED)
        self.create_subscription(LaserScan, 'scan', lambda m: setattr(self, 'scan', m), qos_profile_sensor_data)
        # volatile on purpose: slam_toolbox's pose is volatile, AMCL's is transient-local; a volatile reader takes both
        # (2026-10-06: the latched reader never got one pose from slam_toolbox - "incompatible QoS: DURABILITY")
        self.create_subscription(PoseWithCovarianceStamped, 'amcl_pose', lambda m: setattr(self, 'amcl', m), 1)
        self.create_subscription(PoseWithCovarianceStamped, 'localizer/result',
                                 lambda m: setattr(self, 'loc_result', m), 1)
        # standing = wheels disabled (the driver's state, ~1 Hz); an odometry subscription in Python cost ~10 % of a core
        self.create_subscription(String, 'rosorin_board_driver/state', self.on_driver, 5)
        c = self.create_client
        self.loc_active, self.loc_manage = (c(Trigger, 'lifecycle_manager_localization/is_active'),
                                            c(ManageLifecycleNodes, 'lifecycle_manager_localization/manage_nodes'))
        self.nav_active, self.nav_manage = (c(Trigger, 'lifecycle_manager_navigation/is_active'),
                                            c(ManageLifecycleNodes, 'lifecycle_manager_navigation/manage_nodes'))
        self.grid_search = c(Empty, 'trigger_grid_search_localization')
        self.nomotion = c(Empty, 'request_nomotion_update')
        self.slam_mode = c(SetBool, 'slam_toolbox/set_localization_mode')
        self.serialize = c(SerializePoseGraph, 'slam_toolbox/serialize_map')
        self.savemap = c(SaveMap, 'slam_toolbox/save_map')
        self.publish()

    # ---- inputs
    def on_map(self, m):
        # slam_toolbox republishes the map every map_update_interval even when nothing changed: rebuild the matcher
        # only for a different map (the data, not the stamp)
        key = (m.info.width, m.info.height, m.info.origin.position.x, m.info.origin.position.y, hash(bytes(m.data)))
        if self.map is not None and key == self.map_key:
            return
        self.map, self.map_key, self.matcher = m, key, ScanMatcher(m)
        self.get_logger().info(f'map {m.info.width}x{m.info.height} at {m.info.resolution} m, {len(self.matcher.free)} free cells')

    def on_driver(self, m):
        try:
            d = json.loads(m.data)
            self.wheels_on, self.plugged = bool(d.get('enabled')), bool(d.get('plugged'))
            if self.wheels_on:
                self.moving_t = time.time()
        except ValueError:
            pass

    def points(self):
        """The newest scan as points in base_link, or None when there is no fresh scan."""
        s = self.scan
        if s is None or (self.get_clock().now() - Time.from_msg(s.header.stamp)).nanoseconds > 1.5e9:
            return None
        r = np.asarray(s.ranges, np.float32)
        ok = np.isfinite(r) & (r > max(s.range_min, 0.12)) & (r < s.range_max)
        a = s.angle_min + np.arange(len(r)) * s.angle_increment
        if self.laser is None:                 # fixed mount: read once, then stop listening to /tf (a Python TF
            try:                               # listener on the full /tf stream cost ~25 % of a core here)
                tf = self.buf.lookup_transform('base_link', s.header.frame_id, Time()).transform
            except Exception:
                return None
            self.laser = tf.translation.x, tf.translation.y, yaw_of(tf.rotation)
            self.tfl.unregister(); self.tfl = None
        lx, ly, lyaw = self.laser
        a = a[ok] + lyaw
        return np.stack([lx + r[ok] * np.cos(a), ly + r[ok] * np.sin(a)], 1) if ok.sum() >= 60 else None

    def amcl_pose(self):
        if self.amcl is None:
            return None
        p = self.amcl.pose.pose
        return np.array([p.position.x, p.position.y, yaw_of(p.orientation)])

    # ---- outputs
    def publish(self, state=None, **info):
        if state:
            if state != self.state:
                self.get_logger().info(f'{self.state} -> {state} {info or ""}')
            self.state, self.info = state, info
        p = self.amcl_pose()
        d = dict(state=self.state, ready=self.state == 'ready', heals=self.heals, t=round(time.time(), 1),
                 pose=None if p is None else [round(float(p[0]), 3), round(float(p[1]), 3), round(math.degrees(p[2]), 1)],
                 **self.info)
        self.pub.publish(String(data=json.dumps(d)))

    def call(self, client, req, timeout):
        if not client.wait_for_service(timeout_sec=min(timeout, 5.0)):
            return None
        ev = threading.Event(); f = client.call_async(req); f.add_done_callback(lambda _: ev.set())
        if not ev.wait(timeout):
            client.remove_pending_request(f)
            return None
        return f.result()

    def active(self, client):
        r = self.call(client, Trigger.Request(), 4.0)
        return None if r is None else r.success

    def loc_alive(self):
        """Localization is there: slam_toolbox answers (and the filter manager is active when there is one), or the
        AMCL manager is active."""
        if self.slam:
            if not self.slam_mode.wait_for_service(timeout_sec=2.0):
                return False
            if not self.loc_manager:
                return True
        return self.active(self.loc_active)

    def set_mapping(self, on):
        """slam_toolbox: mapping continues the kept graph with the scans of this drive; localization only tracks the
        pose on it. Mapping only while the pose is confirmed (a wrong start pose spoils the map: whole_project_review
        section 4); back to localization the moment the pose is lost."""
        if not self.slam or self.mapping == on:
            return True
        # the installed node (2.6.10) starts in mapping whatever `mode` says, and only this service creates its
        # /initialpose subscriber (checked live 2026-10-06): the first call is always made explicitly
        r = self.call(self.slam_mode, SetBool.Request(data=not on), 5.0)
        if r is None:
            return False
        self.mapping = on
        self.get_logger().info('slam_toolbox: ' + ('mapping - the kept map grows with this drive' if on else 'localization only'))
        return True

    def keep_map(self, why):
        """Copies of the map the robot carries: the pose graph (what it continues from) and the image + yaml (GPU
        localizer, keepout mask alignment). The previous copies go to history/<stamp>/ (10 kept). My part of the map:
        the robot makes it, I keep the copies."""
        self.save_t = time.time()
        stamp = time.strftime('%Y%m%d_%H%M%S')
        hist = os.path.join(self.map_dir, 'history', stamp)
        try:
            os.makedirs(hist, exist_ok=True)
            for f in ('room.posegraph', 'room.data', 'room_live.pgm', 'room_live.png', 'room_live.yaml'):
                src = os.path.join(self.map_dir, f)
                if os.path.exists(src):
                    shutil.copy2(src, hist)
            old = sorted(d for d in glob.glob(os.path.join(self.map_dir, 'history', '2*')) if os.path.isdir(d))
            for d in old[:-10]:
                shutil.rmtree(d, ignore_errors=True)
        except OSError as e:
            self.get_logger().warn(f'map history copy failed: {e}')
        r1 = self.call(self.serialize, SerializePoseGraph.Request(filename=os.path.join(self.map_dir, 'room')), 30.0)
        r2 = self.call(self.savemap, SaveMap.Request(name=String(data=os.path.join(self.map_dir, 'room_live'))), 30.0)
        ok = r1 is not None and r1.result == 0 and r2 is not None and r2.result == 0
        self.get_logger().info(f'map kept ({why}): graph {"ok" if r1 is not None and r1.result == 0 else "FAILED"}, '
                               f'image {"ok" if r2 is not None and r2.result == 0 else "FAILED"}, previous in {hist}')
        return ok

    def manage(self, client, command, timeout):
        """True / False = the manager's answer; None = it did not answer in time."""
        r = self.call(client, ManageLifecycleNodes.Request(command=command), timeout)
        return None if r is None else bool(r.success)

    # ---- the pose chain
    def seeds(self):
        out = []
        p = self.amcl_pose()
        if p is not None:
            out.append(('amcl', p))
        for name, f in (('remembered', self.pose_file), ('home', self.home_file)):
            try:
                h = json.load(open(f)); out.append((name, np.array([h['x'], h['y'], h['yaw']], float)))
            except (OSError, ValueError, KeyError):
                pass
        return out

    def place(self):
        """A checked pose for AMCL, or None. Standing still only.
        Rule (replay of a recorded run, scripts/tests/scan_fit_replay.py, 2026-10-05): the fit alone does not protect -
        near a wall other places fit 0.83-0.98. What protects is the MARGIN over the best other place: fit >= 0.65 and
        margin >= 0.10 accepted 17 of 86 scans, none at a wrong place; fit >= 0.75 and margin >= 0.05 accepted 5 wrong.
        A remembered pose (where it stood when last localized) may also be kept when nothing else fits clearly better."""
        self.set_mapping(False)                                 # seeds are taken in localization mode only
        pts = self.points()
        if pts is None or self.matcher is None:
            return self.publish('localizing', why='waiting for map and scan')
        self.accept = self.get_parameter('accept_fit').value          # live: `ros2 param set` for tests
        self.margin = self.get_parameter('search_margin').value
        t0 = time.time()
        best, f, second = self.matcher.search(pts)
        tried = {'search': [round(f, 2), round(second, 2), round(time.time() - t0, 1)]}
        near = lambda p: best is not None and math.hypot(p[0] - best[0], p[1] - best[1]) < 0.4 and \
            abs(math.remainder(p[2] - best[2], math.tau)) < 0.5
        seeds = self.seeds()
        self.loc_result = None                                  # NVIDIA GPU search: one more candidate, never trusted alone
        if self.call(self.grid_search, Empty.Request(), 30.0) is not None:
            end = time.time() + 6
            while self.loc_result is None and time.time() < end:
                time.sleep(0.2)
            if self.loc_result is not None:
                q = self.loc_result.pose.pose
                seeds.append(('gpu_localizer', np.array([q.position.x, q.position.y, yaw_of(q.orientation)])))
        agree = []
        for name, seed in seeds:
            p, sf = self.matcher.refine(seed, pts)
            tried[name] = round(sf, 2)
            if near(p):
                agree.append(name)
            if name in ('amcl', 'remembered') and sf >= self.accept and sf >= f - 0.03:
                return self.seed_amcl(p, sf, name + ' (nothing else fits clearly better)')
        if best is not None and f >= self.accept and f - second >= self.margin:
            return self.seed_amcl(best, f, 'search' + (' + ' + ', '.join(agree) if agree else ''))
        self.publish('lost', why='no single place explains the scan', tried=tried, points=int(len(pts)))

    def seed_amcl(self, p, fit, source):
        m = PoseWithCovarianceStamped(); m.header.frame_id = 'map'
        m.pose.pose.position.x, m.pose.pose.position.y = float(p[0]), float(p[1])
        m.pose.pose.orientation.z, m.pose.pose.orientation.w = math.sin(p[2] / 2), math.cos(p[2] / 2)
        m.pose.covariance[0] = m.pose.covariance[7] = 0.02; m.pose.covariance[35] = 0.01
        end = time.time() + 15
        while time.time() < end:
            m.header.stamp = self.get_clock().now().to_msg(); self.init_pub.publish(m)
            time.sleep(0.5)
            if not self.slam:
                self.call(self.nomotion, Empty.Request(), 2.0)
            a = self.amcl_pose()
            if a is not None and math.hypot(a[0] - p[0], a[1] - p[1]) < 0.25 and \
                    abs(math.remainder(a[2] - p[2], math.tau)) < 0.3:
                self.placed = True; self.low = 0
                self.get_logger().info(f'placed by {source}: x {p[0]:.2f} y {p[1]:.2f} yaw {math.degrees(p[2]):.0f} deg, fit {fit:.2f}')
                return True
        self.publish('localizing', why='AMCL did not take the pose', source=source)

    def probe_plan(self):
        """Ready means navigation can plan from where it stands: ask the planner for a path to the robot's own pose.
        2026-10-05/06: three times every navigation server stopped receiving AMCL's map->odom transform (a fresh
        listener still got it; the servers' own copy stayed at one stamp for hours). Standing still nothing logs it;
        with a goal the controller refuses ("Transform data too old") and the planner fails. Only a restart of the
        service brought it back. This probe is the cheapest honest question (one plan on a 90x140 map, every
        PROBE_EVERY s); two failures in a row -> restart. Returns True/False, None = no answer in time."""
        p = self.amcl_pose()
        if p is None:
            return None
        if self.plan_client is None:
            self.plan_client = ActionClient(self, ComputePathToPose, 'compute_path_to_pose')
        if not self.plan_client.wait_for_server(timeout_sec=2.0):
            return None
        g = ComputePathToPose.Goal(); g.use_start = False
        g.goal.header.frame_id = 'map'; g.goal.header.stamp = self.get_clock().now().to_msg()
        g.goal.pose.position.x, g.goal.pose.position.y = float(p[0]), float(p[1])
        g.goal.pose.orientation.z, g.goal.pose.orientation.w = math.sin(p[2] / 2), math.cos(p[2] / 2)
        fut = self.plan_client.send_goal_async(g)
        end = time.time() + 10.0
        while not fut.done() and time.time() < end:
            time.sleep(0.1)
        if not fut.done() or fut.result() is None or not fut.result().accepted:
            return None
        rf = fut.result().get_result_async()
        while not rf.done() and time.time() < end:
            time.sleep(0.1)
        if not rf.done() or rf.result() is None:
            return None
        res = rf.result()
        if not (res.status == 4 and len(res.result.path.poses) > 0):     # 4 = STATUS_SUCCEEDED
            return False
        # 2026-10-06 10:22 UTC: the planner still planned while the controller was deaf (its own transform copy stale
        # for 6 min, probe at 0 fails). So the controller gets the question too: follow that path to the pose it is
        # already at. Only while the wheels are disabled (the driver then ignores cmd_vel; nothing moves, no skill is
        # preempted). Deaf -> "Transform data too old" -> ABORTED.
        if self.wheels_on:
            return True
        if self.follow_client is None:
            self.follow_client = ActionClient(self, FollowPath, 'follow_path')
        if not self.follow_client.wait_for_server(timeout_sec=2.0):
            return None
        fg = FollowPath.Goal(); fg.path = res.result.path
        fut = self.follow_client.send_goal_async(fg)
        end = time.time() + 10.0
        while not fut.done() and time.time() < end:
            time.sleep(0.1)
        if not fut.done() or fut.result() is None or not fut.result().accepted:
            return None
        gh = fut.result(); rf = gh.get_result_async()
        while not rf.done() and time.time() < end:
            time.sleep(0.1)
        if not rf.done():
            gh.cancel_goal_async(); return None
        st = rf.result().status
        return True if st == 4 else (False if st == 6 else None)          # 6 = STATUS_ABORTED

    def watch_pose(self):
        """While ready: is the pose still explained by the scan? Remember it when it is."""
        pts, p = self.points(), self.amcl_pose()
        if pts is None or p is None or self.matcher is None:
            return None
        f = float(self.matcher.fits(p[None, :], pts)[0])
        if f >= 0.60 and (self.saved is None or math.hypot(p[0] - self.saved[0], p[1] - self.saved[1]) > 0.05
                          or abs(math.remainder(p[2] - self.saved[2], math.tau)) > 0.05):
            tmp = self.pose_file + '.tmp'
            json.dump(dict(x=float(p[0]), y=float(p[1]), yaw=float(p[2]), fit=round(f, 2), t=time.time()), open(tmp, 'w'))
            os.replace(tmp, self.pose_file); self.saved = p
        standing = time.time() - self.moving_t > 3.0
        self.low = self.low + 1 if (f < self.lost_fit and standing) else 0
        if self.low >= self.lost_checks:
            self.placed = False
            self.publish('localizing', why=f'scan no longer fits the pose (fit {f:.2f})')
        return f

    # ---- the loop
    def run(self):
        """Returns the exit code: 1 = restart all of navigation (systemd does it; ready again in ~15 s)."""
        nav_started, down_since, silent = False, None, 0
        while rclpy.ok():
            time.sleep(1.0)
            loc = self.loc_alive()
            if not loc:
                down_since = down_since or time.time()
                self.publish('starting' if not nav_started else 'healing', why='localization servers not active')
                if time.time() - down_since > 90:               # its own autostart had its time
                    return self.restart('localization servers did not become active in 90 s')
                continue
            down_since = None
            if not self.placed:
                if time.time() - self.moving_t > 3.0:
                    self.place()
                else:
                    self.publish('localizing', why='wheels enabled')
                if not self.placed:
                    self.lost_n += 1                             # standing and lost: the scan will not change by itself;
                    time.sleep(min(60.0, 4.0 * self.lost_n))     # look again less and less often (a search costs 1.6 s of CPU)
                    continue
                self.lost_n = 0
            nav = self.active(self.nav_active)
            if nav is None:
                # A manager that does not answer is stuck inside a lifecycle call: on Humble it waits without limit for
                # a server that froze (measured 2026-10-05: controller_server stopped 25 s -> bond broken -> the manager
                # hung in "Deactivating controller_server" for minutes). No service can heal that; restart.
                silent += 1
                self.publish('healing', why='navigation manager does not answer')
                if silent >= 5:
                    return self.restart('navigation manager silent for 25 s')
                continue
            silent = 0
            if not nav:
                self.publish('starting_nav' if not nav_started else 'healing', why='navigation servers not active')
                ok = self.manage(self.nav_manage, ManageLifecycleNodes.Request.STARTUP, 120.0) if not nav_started \
                    else self.heal(self.nav_manage, 'navigation')
                nav_started = True
                if ok is None:
                    return self.restart('navigation manager did not answer a lifecycle request')
                if not ok:
                    self.nav_fails += 1
                    self.get_logger().error(f'navigation did not come up ({self.nav_fails}/2)')
                    if self.nav_fails >= 2:
                        return self.restart('navigation did not come up twice')
                continue
            self.nav_fails = 0
            f = self.watch_pose()
            if self.placed:
                # the map grows only on real drives: plugged in (charger, stand) the wheels are not meant to move and any
                # odometry is false (2026-10-06 stand test: 10 s of spinning wheels put phantom nodes into the graph)
                self.set_mapping(not self.plugged)
                if self.slam and self.mapping:
                    standing = time.time() - self.moving_t
                    if (self.wheels_were_on and not self.wheels_on and standing > 5.0) or time.time() - self.save_t > 600.0:   # noqa
                        self.keep_map('after a drive' if self.wheels_were_on else 'every 10 min')
                        self.wheels_were_on = False
                    if self.wheels_on:
                        self.wheels_were_on = True
                if time.time() - self.probe_t > PROBE_EVERY:
                    self.probe_t = time.time()
                    ok = self.probe_plan()
                    self.probe_fails = 0 if ok else self.probe_fails + 1
                    if not ok:
                        self.get_logger().warn(f'planner could not plan from the robot\'s own pose ({self.probe_fails}/2, '
                                               f'{"no answer" if ok is None else "failed"})')
                    if self.probe_fails >= 2:
                        return self.restart('navigation cannot plan from its own pose twice (servers deaf to the map transform?)')
                self.publish('ready', fit=None if f is None else round(f, 2), probe_fails=self.probe_fails, mapping=self.mapping)
                time.sleep(1.0)                                  # ready: one look every 2 s is enough
        return 0

    def heal(self, manage, what):
        """Stock recovery of a half-alive stack: RESET then STARTUP (the manager never retries by itself)."""
        self.heals += 1
        self.get_logger().warn(f'{what}: RESET then STARTUP (heal {self.heals})')
        if self.manage(manage, ManageLifecycleNodes.Request.RESET, 40.0) is None:
            return None
        return self.manage(manage, ManageLifecycleNodes.Request.STARTUP, 120.0)

    def restart(self, why):
        self.get_logger().error(f'restarting navigation: {why}')
        self.publish('healing', why=f'restarting navigation: {why}')
        return 1


def main():
    rclpy.init()
    n = NavSupervisor()
    ex = MultiThreadedExecutor(num_threads=3); ex.add_node(n)
    threading.Thread(target=ex.spin, daemon=True).start()
    rc = 1
    try:
        rc = n.run()
    except (KeyboardInterrupt, ExternalShutdownException):
        rc = 0
    finally:
        if rclpy.ok():
            n.publish('starting', why='supervisor stopped')
            time.sleep(0.3)
            rclpy.shutdown()
    sys.exit(rc)


def wait_main():
    """nav_wait [seconds]: exit 0 as soon as navigation says ready, 1 after the time with the last status printed."""
    limit = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    rclpy.init(); n = rclpy.create_node('nav_wait'); st = {}
    n.create_subscription(String, 'nav/status', lambda m: (st.clear(), st.update(json.loads(m.data))), LATCHED)
    end = time.time() + limit
    ready = lambda: st.get('ready') and time.time() - st.get('t', 0) < 15      # a status that old = supervisor hung
    while time.time() < end and not ready():
        rclpy.spin_once(n, timeout_sec=0.5)
    print(json.dumps(st) if st else 'no navigation status (rosorin-nav not running?)', flush=True)
    ok = bool(ready())
    rclpy.shutdown()
    sys.exit(0 if ok else 1)
