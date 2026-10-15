#!/usr/bin/env python3
"""Stop proof ON THE FLOOR - the second half of the 2026-10-02 gate (docs/lessons.md, docs/motion_safety.md).
Short, slow drives; every stop path timed from MEASURED motion (camera odometry /odom_vslam and LiDAR odometry
/odom_rf2o position change; gyro for rotation), plus the distance travelled after the stop was triggered.

  stop_proof_floor.py look          state + LiDAR clearance all round + one camera frame (/tmp/floor_view.jpg)
  stop_proof_floor.py prove         forward only into a corridor the LiDAR shows clear, back over the same floor

Harness safety (this script is the only cmd_vel publisher): a drive starts only with CLEAR m free in the corridor;
while driving, anything inside GUARD m of the direction of travel -> every stop at once; a stop that has not stopped
the robot FAIL_S after its trigger -> every other stop at once."""
import json, math, os, subprocess, sys, time

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState, Image, Imu, LaserScan
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

DRV = 'rosorin_board_driver'
LASER_X = 0.048
HALF_W = 0.30            # corridor half width (robot half width ~0.15 + margin)
CLEAR = 1.2              # m free ahead needed to start a drive
GUARD = 0.45             # m: anything this close in the direction of travel while moving -> stop everything
FAIL_S = 0.8             # s after a stop trigger with the robot still moving -> the stop FAILED, stop everything
MOVING = 0.04            # m/s measured (position change over WIN) that counts as moving
WIN = 0.3
STILL_GZ = 0.08          # rad/s gyro z that counts as turning


class Rig(Node):
    def __init__(self):
        super().__init__('stop_proof_floor')
        self.scan, self.st, self.bat, self.rgb = None, {}, None, None
        self.vs, self.rf, self.gz, self.st_log = [], [], [], []
        self.create_subscription(LaserScan, 'scan', lambda m: setattr(self, 'scan', m), qos_profile_sensor_data)
        self.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: setattr(self, 'rgb', m), qos_profile_sensor_data)
        self.create_subscription(BatteryState, 'battery', lambda m: setattr(self, 'bat', m.voltage), 10)
        self.create_subscription(String, f'{DRV}/state', self.on_state, 10)
        self.create_subscription(Odometry, 'odom_vslam', lambda m: self.vs.append(self.xy(m)), 50)
        self.create_subscription(Odometry, 'odom_rf2o', lambda m: self.rf.append(self.xy(m)), 50)
        self.create_subscription(Imu, 'imu/data_raw', lambda m: self.gz.append((time.time(), m.angular_velocity.z)), 200)
        self.pub = self.est = None                          # created by drive_mode(): a second cmd_vel publisher trips
                                                            # the driver's e-stop (it did, during a run, 2026-10-02)
        self.en = self.create_client(SetBool, f'{DRV}/enable')
        self.rs = self.create_client(Trigger, f'{DRV}/reset_estop')

    def drive_mode(self):
        self.pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.est = self.create_publisher(Bool, 'estop', 10)

    @staticmethod
    def xy(m):
        return time.time(), m.pose.pose.position.x, m.pose.pose.position.y

    def on_state(self, m):
        self.st = json.loads(m.data)
        self.st_log.append((time.time(), self.st.get('enabled'), self.st.get('estop')))

    def spin(self, s):
        t = time.time()
        while time.time() - t < s:
            rclpy.spin_once(self, timeout_sec=0.01)

    def call(self, cli, req):
        if not cli.wait_for_service(5):
            return None
        f = cli.call_async(req)
        rclpy.spin_until_future_complete(self, f, timeout_sec=20)
        return f.result()

    def enable(self, on):
        r = self.call(self.en, SetBool.Request(data=on))
        return bool(r and r.success)

    def reset(self):
        self.call(self.rs, Trigger.Request())

    def stop_all(self):
        self.pub.publish(Twist())
        self.est.publish(Bool(data=True))
        self.enable(False)

    # ---- what the LiDAR shows, in base_link (x forward, y left)
    def points(self):
        s = self.scan
        r = np.asarray(s.ranges, np.float32)
        ang = s.angle_min + np.arange(len(r)) * s.angle_increment + math.pi
        ok = np.isfinite(r) & (r > 0.12)
        return LASER_X + r * np.cos(ang), r * np.sin(ang), ok

    def corridor(self, sign):
        """(nearest return in the corridor ahead (sign +1) / behind (-1) in m or None, share of valid beams there)."""
        x, y, ok = self.points()
        ahead = sign * x
        c = ok & (ahead > 0.15) & (np.abs(y) < HALF_W)
        cone = np.abs(np.arctan2(y, sign * x)) < math.radians(15)
        return (float(ahead[c].min()) if c.any() else None), float(ok[cone].mean()) if cone.any() else 0.0

    def speed(self, log, t):
        """Measured speed at time t: position change over the last WIN seconds (None without data)."""
        a = [p for p in log if t - WIN <= p[0] <= t]
        if len(a) < 2 or a[-1][0] - a[0][0] < WIN * 0.5:
            return None
        return math.hypot(a[-1][1] - a[0][1], a[-1][2] - a[0][2]) / (a[-1][0] - a[0][0])

    def moved(self, log, t0, t1):
        a = [p for p in log if p[0] <= t0][-1:] + [p for p in log if t0 < p[0] <= t1]
        return math.hypot(a[-1][1] - a[0][1], a[-1][2] - a[0][2]) if len(a) >= 2 else float('nan')

    def turn(self, t):
        a = [g[1] for g in self.gz if t - 0.15 <= g[0] <= t]
        return abs(float(np.mean(a))) if a else None


def look(n):
    print('state', {k: n.st.get(k) for k in ('enabled', 'estop', 'plugged', 'arm_enabled')}, 'battery', n.bat,
          '| vslam msgs', len(n.vs), 'rf2o msgs', len(n.rf))
    x, y, ok = n.points()
    bear, d = np.degrees(np.arctan2(y, x)), np.hypot(x, y)
    print(f'LiDAR beams {len(d)}, valid {100 * ok.mean():.0f} %')
    for c in range(-180, 180, 30):
        sel = np.abs(((bear - c + 180) % 360) - 180) < 15
        v = sel & ok
        near = f'{d[v].min():.2f}' if v.any() else ' nan'
        print(f'  bearing {c:+4d} deg: valid {int(v.sum()):3d}/{int(sel.sum()):3d}  nearest {near} m')
    for name, sign in (('ahead', 1), ('behind', -1)):
        near, valid = n.corridor(sign)
        print(f'corridor {name}: nearest return {near}, valid beams in +-15 deg {100 * valid:.0f} %')
    if n.rgb is not None:
        m = n.rgb
        cv2.imwrite('/tmp/floor_view.jpg', np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)[:, :, :3])


def run(n, name, kw, path, drive_s=1.5):
    """One drive + one stop path. Returns True/False (stopped as required) or None (not run)."""
    lin = bool(kw.get('vx'))
    sign = 1 if kw.get('vx', 0) >= 0 else -1
    if lin and sign > 0:                                    # forward goes into floor not yet covered: must be clear
        near, valid = n.corridor(1)
        if (near is not None and near < CLEAR) or valid < 0.6:
            print(f'{name}: NOT RUN - corridor ahead: nearest {near}, valid beams {100 * valid:.0f} %', flush=True)
            return None
    if n.st.get('estop'):
        n.reset(); n.spin(0.3)
    if not n.st.get('enabled'):
        if not n.enable(True):
            print(f'{name}: NOT RUN - enable refused {n.st.get("estop")}', flush=True); return None
        n.spin(4.0)                                         # arm to the drive pose, camera odometry settles
    m = Twist(); m.linear.x, m.angular.z = float(kw.get('vx', 0.0)), float(kw.get('wz', 0.0))

    def measured(t):
        if not lin:
            g = n.turn(t)
            return None if g is None else (g > STILL_GZ)
        # camera odometry first, LiDAR odometry when there is none. 2026-10-12: rf2o on /scan_rf2o (without the
        # self-returns that pinned it) jitters standing: 0.3 s windows median 0.016 m/s, 12 % over MOVING, and the
        # judge called a real stop "MOVED AGAIN"; the camera read 0.005 m/s at most and the same travel as rf2o.
        # LiDAR odometry only when the camera is silent for 1 s, not for one skipped frame (a skipped frame fell
        # back to rf2o's jitter and failed a stop the camera and the IMU saw holding).
        sp = n.speed(n.vs, t)
        if sp is None and not (n.vs and t - n.vs[-1][0] < 1.0):
            sp = n.speed(n.rf, t)
        return None if sp is None else (sp > MOVING)

    def guard():
        if not lin:
            return False
        near, _ = n.corridor(sign)
        return near is not None and near < GUARD

    t0 = time.time()
    while time.time() - t0 < drive_s:                       # the drive
        n.pub.publish(m); n.spin(0.05)
        if guard():
            n.stop_all(); print(f'{name}: ABORTED - something within {GUARD} m in the direction of travel', flush=True)
            return None
    was_moving = measured(time.time())
    peak = max([s for s in (n.speed(n.vs, time.time()), n.speed(n.rf, time.time())) if s is not None], default=None)
    t_stop = time.time()                                    # the stop
    proc = None
    if path == 'estop':
        n.est.publish(Bool(data=True))
    elif path == 'disable':
        n.enable(False)
    elif path == 'kill':
        proc = subprocess.Popen("pkill -KILL -f 'lib/rosorin_base/board_driver'", shell=True)
    elif path == 'service':
        proc = subprocess.Popen('sudo systemctl stop rosorin-base', shell=True)
    stopped_at, failed = None, False
    while time.time() - t_stop < 3.0:
        if path != 'deadman' and not failed:
            n.pub.publish(m)                                # commands keep coming: the stop must hold against them
        n.spin(0.05)
        t = time.time()
        mv = measured(t)
        if stopped_at is None and mv is False and t - t_stop >= 0.1:
            stopped_at = t
        if stopped_at is None and t - t_stop > FAIL_S and not failed:
            failed = True
            n.stop_all()
            print(f'{name}: STILL MOVING {FAIL_S} s after the stop - all stops fired', flush=True)
        if stopped_at is not None and mv and t - stopped_at > WIN + 0.1:
            failed = True
            n.stop_all()
            print(f'{name}: MOVED AGAIN after stopping - all stops fired', flush=True)
            break
    if proc is not None:
        proc.wait()
        if path == 'service':
            subprocess.run('sudo systemctl start rosorin-base', shell=True)
        t_w = time.time(); n.st = {}
        while time.time() - t_w < 40 and not n.st:          # driver back (disabled) before anything else
            n.spin(0.5)
        n.spin(2.0)
    t_end = t_stop + 3.0
    if lin:
        dist = f'travel after the trigger: camera {100 * n.moved(n.vs, t_stop, t_end):.1f} cm, LiDAR {100 * n.moved(n.rf, t_stop, t_end):.1f} cm'
        spd = f'{peak:.2f} m/s measured' if peak is not None else 'no speed data'
    else:
        dist, spd = '', 'turning' if was_moving else 'not turning'
    ok = bool(was_moving) and stopped_at is not None and not failed
    took = f'{stopped_at - t_stop:.2f} s' if stopped_at else 'never'
    print(f'{name:28s} before: {spd}; measured still after {took}; {dist}  {"PASS" if ok else "FAIL"}', flush=True)
    if path == 'estop':
        n.reset(); n.spin(0.3)
    return ok


def prove(n):
    plan = [('deadman 0.1 fwd', dict(vx=0.1), 'deadman'), ('estop 0.1 back', dict(vx=-0.1), 'estop'),
            ('disable 0.1 fwd', dict(vx=0.1), 'disable'), ('deadman 0.1 back', dict(vx=-0.1), 'deadman'),
            ('estop 0.2 fwd', dict(vx=0.2), 'estop'), ('deadman 0.2 back', dict(vx=-0.2), 'deadman'),
            ('disable 0.2 fwd', dict(vx=0.2), 'disable'), ('estop 0.2 back', dict(vx=-0.2), 'estop'),
            ('estop turn 0.5', dict(wz=0.5), 'estop'), ('deadman turn -0.5', dict(wz=-0.5), 'deadman'),
            ('driver kill 0.2 fwd', dict(vx=0.2), 'kill'), ('deadman 0.2 back', dict(vx=-0.2), 'deadman'),
            ('service stop 0.2 fwd', dict(vx=0.2), 'service'), ('disable 0.2 back', dict(vx=-0.2), 'disable')]
    only = sys.argv[2].split(',') if len(sys.argv) > 2 else None
    res, last_fwd = [], None
    for name, kw, path in plan:
        if only and path not in only and not (kw.get('vx', 0) < 0):
            continue
        if kw.get('vx', 0) < 0 and last_fwd is not True and last_fwd is not False:
            continue                                        # back only over floor a forward drive just covered
        r = run(n, name, kw, path)
        if kw.get('vx', 0) > 0:
            last_fwd = r
        elif kw.get('vx', 0) < 0:
            last_fwd = None
        if r is not None:
            res.append(r)
        if r is False:
            print('STOPPING THE PROOF: a stop failed', flush=True); break
        n.spin(1.0)
    print(f'RESULT: {sum(res)}/{len(res)} passed', flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'look'
    rclpy.init()
    n = Rig()
    t = time.time()
    while (not n.st or n.scan is None) and time.time() - t < 15:
        n.spin(0.5)
    n.spin(2.0)
    if not n.st or n.scan is None:
        print('ABORT: no driver state or no LiDAR - nothing sent'); return
    if mode == 'look':
        look(n); return
    if n.st.get('plugged'):
        print('ABORT: plugged in - no driving'); return
    n.drive_mode()
    if not n.vs and not n.rf:
        print('ABORT: no measured odometry (vslam, rf2o) - nothing sent'); return
    try:
        prove(n)
    finally:
        n.pub.publish(Twist())
        n.enable(False)
        n.spin(0.3)
        print('end state:', {k: n.st.get(k) for k in ('enabled', 'estop')}, 'battery', n.bat)


if __name__ == '__main__':
    main()
