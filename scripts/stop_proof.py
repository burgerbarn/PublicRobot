#!/usr/bin/env python3
"""Stop proof ON THE STAND (wheels in the air) - docs/lessons.md 2026-10-02 gate.
Physical evidence, not software state: the IMU (110 Hz) and the battery voltage (1 Hz) are recorded while the wheels
spin and through each stop, so "stopped" is what the sensors show, with the time it took.

  stop_proof.py characterize        spin / idle phases -> what the sensors show for spinning vs still
  stop_proof.py prove [paths]       each stop path: deadman, estop, disable, scanloss, kill, service, contact

Run on the robot with rosorin-contact stopped (its reflex e-stops any spin on the stand; it is its own test)."""
import json, math, os, subprocess, sys, termios, time

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

DRV = 'rosorin_board_driver'
DEV = '/dev/ttyACM0'


def raw_watch(n, seconds, open_wait=4.0):
    """While board_driver is gone, read the board's IMU frames straight from the port (it streams them unasked), so
    the wheels are still observed. Returns (first, last) sample time or None if the port never opened."""
    from rosorin_base.rrc_protocol import FUNC_IMU, FrameParser, decode_imu
    t0, fd = time.time(), None
    while time.time() - t0 < open_wait:
        try:
            fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK); break
        except OSError:
            time.sleep(0.01)
    if fd is None:
        return None
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B1000000
    termios.tcsetattr(fd, termios.TCSANOW, a)
    parser, first, last, t1 = FrameParser(), None, None, time.time() + seconds
    while time.time() < t1:
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            time.sleep(0.002); continue
        except OSError:
            break
        now = time.time()
        for func, payload in parser.feed(data):
            if func == FUNC_IMU and len(payload) == 24:
                (ax, ay, az), (gx, gy, gz) = decode_imu(payload)
                n.imu.append((now, ax * 9.80665, ay * 9.80665, az * 9.80665,
                              math.radians(gx), math.radians(gy), math.radians(gz)))
                first, last = first or now, now
    os.close(fd)
    n.imu.sort(key=lambda r: r[0])
    return first, last


class Rig(Node):
    def __init__(self):
        super().__init__('stop_proof')
        self.imu, self.bat, self.st, self.st_log = [], [], {}, []
        self.create_subscription(Imu, 'imu/data_raw', self.on_imu, 200)
        self.create_subscription(BatteryState, 'battery', lambda m: self.bat.append((time.time(), m.voltage)), 10)
        self.create_subscription(String, f'{DRV}/state', self.on_state, 10)
        self.pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.est = self.create_publisher(Bool, 'estop', 10)
        self.en = self.create_client(SetBool, f'{DRV}/enable')
        self.rs = self.create_client(Trigger, f'{DRV}/reset_estop')

    def on_imu(self, m):
        a, g = m.linear_acceleration, m.angular_velocity
        self.imu.append((time.time(), a.x, a.y, a.z, g.x, g.y, g.z))

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
        return r and r.success

    def reset(self):
        self.call(self.rs, Trigger.Request())

    def drive(self, s, vx=0.0, vy=0.0, wz=0.0):
        m = Twist(); m.linear.x, m.linear.y, m.angular.z = vx, vy, wz
        t = time.time()
        while time.time() - t < s:
            self.pub.publish(m); self.spin(0.05)

    def vib(self, t0, t1):
        """Vibration between two times: std of each IMU channel after removing its mean (accel m/s^2, gyro rad/s)."""
        a = np.array([r for r in self.imu if t0 <= r[0] < t1])
        return None if len(a) < 20 else a[:, 1:].std(axis=0)

    def volts(self, t0, t1):
        v = [b[1] for b in self.bat if t0 <= b[0] < t1]
        return float(np.mean(v)) if v else float('nan')


def fmt(v):
    return 'none' if v is None else ' '.join(f'{x:.4f}' for x in v)


def characterize(n):
    print('phase                 accel std x y z | gyro std x y z            | battery V')
    def phase(name, s, **kw):
        t0 = time.time()
        n.drive(s, **kw) if kw else n.spin(s)
        t1 = time.time()
        print(f'{name:20s}  {fmt(n.vib(t0 + 1.0, t1))} | {n.volts(t0 + 1.0, t1 + 0.5):.3f}', flush=True)
    phase('idle (disabled)', 5)
    if not n.enable(True):
        print('enable refused:', n.st); return
    n.spin(3.0)                                                     # arm settles in the drive pose
    phase('enabled, still', 5)
    for v in (0.1, 0.2, 0.35):
        phase(f'forward {v} m/s', 5, vx=v)
        phase('still', 4)
    phase('rotate 1.0 rad/s', 5, wz=1.0)
    phase('still', 4)
    n.enable(False)
    phase('disabled', 4)


THR = 0.0035       # gyro x,y vibration (rad/s): still ~0.0016, wheels at 0.2 m/s ~0.006, 0.35 m/s ~0.009 (characterize)
WIN = 0.2


def score(w):
    return float(np.hypot(w[:, 4].std(), w[:, 5].std()))


def windows(n, t0, t1):
    a = np.array([r for r in n.imu if t0 <= r[0] < t1])
    out, t = [], t0
    while t + WIN <= t1 and len(a):
        w = a[(a[:, 0] >= t) & (a[:, 0] < t + WIN)]
        if len(w) >= 10:
            out.append((t + WIN, score(w)))
        t += 0.05
    return out


def judge(n, name, t_stop, t_end, spin_score, need_quiet=2.0):
    """Physical result of one stop: the time from which the vibration STAYS at the still level (2026-10-02: the
    first quiet 0.2 s window is not enough - at 0.2 m/s the spinning level is close to the threshold and a window can
    dip under it while the wheels still turn; that printed "still within 0.10 s" followed by a FAIL)."""
    w = [(t, sc) for t, sc in windows(n, t_stop - WIN, t_end) if t >= t_stop]
    if not w:
        gap = [r[0] for r in n.imu if t_stop <= r[0] < t_end]
        print(f'{name:34s} NO IMU DATA after the stop ({len(gap)} samples in {t_end - t_stop:.1f} s)  FAIL', flush=True)
        return False
    loud = [t for t, sc in w if sc >= THR]
    settled = w[0][0] if not loud else next((t for t, sc in w if t > loud[-1]), None)
    if settled is None:
        print(f'{name:34s} spinning {spin_score:.4f} -> NEVER settled in {t_end - t_stop:.1f} s  FAIL', flush=True)
        return False
    after = [sc for t, sc in w if t >= settled]
    quiet_s = t_end - settled
    ok = spin_score > THR and settled - t_stop <= 2.0 and quiet_s >= need_quiet
    print(f'{name:34s} spinning {spin_score:.4f} -> still from {settled - t_stop:.2f} s on; worst after {max(after):.4f} '
          f'over {quiet_s:.1f} s  {"PASS" if ok else "FAIL"}', flush=True)
    return ok


def spin_up(n, kw, s=3.0):
    if n.st.get('estop'):
        n.reset(); n.spin(0.3)
    if not n.st.get('enabled'):
        if not n.enable(True):
            print('enable refused:', n.st); return None
        n.spin(4.0)                                          # arm to the drive pose, settle
    t0 = time.time()
    n.drive(s, **kw)
    a = np.array([r for r in n.imu if t0 + 1.0 <= r[0] < t0 + s])
    if len(a) < 10:
        print('no IMU data while spinning up (driver gone?)', flush=True); return None
    return score(a)


def prove(n, only=None):
    motions = [('fwd 0.2', dict(vx=0.2)), ('fwd 0.35', dict(vx=0.35)), ('rot 1.0', dict(wz=1.0))]
    res = []
    def want(p):
        return only is None or p in only
    for mname, kw in motions:
        if want('deadman') and (sp := spin_up(n, kw)) is not None:
            t = time.time(); n.spin(5.0)                     # silence: no cmd_vel
            res.append(judge(n, f'deadman (silence), {mname}', t, time.time(), sp))
        if want('estop') and (sp := spin_up(n, kw)) is not None:
            t = time.time(); n.est.publish(Bool(data=True))
            n.drive(5.0, **kw)                               # commands keep coming: must stay stopped
            res.append(judge(n, f'estop topic, {mname}', t, time.time(), sp))
            n.reset(); n.spin(0.5)
        if want('disable') and (sp := spin_up(n, kw)) is not None:
            t = time.time(); n.enable(False)
            n.drive(5.0, **kw)
            res.append(judge(n, f'enable false, {mname}', t, time.time(), sp))
    if want('scanloss'):                                     # LiDAR silent while commands keep coming (2026-10-02):
        sp = spin_up(n, dict(vx=0.35))                       # the driver's scan gate must stop the wheels, and release
        if sp is not None:                                   # them by itself once the reader is back (launch respawn)
            t = time.time()
            subprocess.run("pkill -KILL -f 'lib/rosorin_base/[l]idar_reader'", shell=True)
            n.drive(2.3, vx=0.35)                            # commands never stop; scans return ~3 s after the kill
            res.append(judge(n, 'LiDAR silent (scan gate), fwd 0.35', t, time.time(), sp, need_quiet=1.0))
            n.drive(4.5, vx=0.35)
            t1 = time.time()
            a = np.array([r for r in n.imu if t1 - 2.0 <= r[0] < t1])
            back = score(a) if len(a) > 10 else float('nan')
            ok = back > THR and not n.st.get('estop') and bool(n.st.get('enabled'))
            n.enable(False); n.spin(1.0)
            print(f'{"LiDAR back: wheels released by themselves":34s} vibration {back:.4f} (spinning > {THR})  '
                  f'{"PASS" if ok else "FAIL"}', flush=True)
            res.append(ok)
    for path, cmd in (('kill', "pkill -KILL -f 'lib/rosorin_base/board_driver'"),
                      ('service', 'sudo systemctl stop rosorin-base')):
        if not want(path):
            continue
        sp = spin_up(n, dict(vx=0.35))
        if sp is None:
            continue
        n.imu.clear()
        t = time.time()
        proc = subprocess.Popen(cmd, shell=True)
        seen = raw_watch(n, 3.5)                             # the driver is gone: watch the board directly
        proc.wait()
        if seen is None or seen[0] is None:
            print(f'{path}: could not read the board directly - not measured  FAIL', flush=True); res.append(False)
        else:
            print(f'{path}: board IMU read directly from {seen[0] - t:.2f} to {seen[1] - t:.2f} s after the command',
                  flush=True)
            res.append(judge(n, f'driver {path}, fwd 0.35', t, seen[1], sp))
        if path == 'service':
            subprocess.run('sudo systemctl start rosorin-base', shell=True)
        t_wait = time.time(); n.st = {}
        while time.time() - t_wait < 40 and not n.st:        # driver back (disabled) before the next path
            n.spin(0.5)
        n.spin(2.0)
    if want('contact'):                                      # needs rosorin-contact running: it must trip on its own
        if n.st.get('estop'):
            n.reset(); n.spin(0.3)
        if n.enable(True):
            n.spin(4.0)
            t0 = time.time()
            n.drive(7.0, vx=0.2)
            trip = next((e for e in n.st_log if e[0] > t0 and e[2]), None)
            if trip is None:
                print('contact reflex: NO e-stop in 7 s of wheels turning with no motion  FAIL', flush=True)
                res.append(False)
            else:
                a = np.array([r for r in n.imu if t0 + 0.7 <= r[0] < trip[0]])
                sp = score(a) if len(a) > 10 else float('nan')
                print(f'contact reflex: e-stop "{trip[2]}" {trip[0] - t0:.2f} s after the wheels started', flush=True)
                res.append(judge(n, 'contact reflex, fwd 0.2', trip[0], time.time(), sp))
            n.reset(); n.spin(0.3)
    print(f'RESULT: {sum(res)}/{len(res)} passed', flush=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'characterize'
    rclpy.init()
    n = Rig()
    t = time.time()
    while not n.st and time.time() - t < 15:
        n.spin(0.5)
    n.spin(1.5)
    if not n.st:
        print('ABORT: no driver state received - nothing sent'); return
    print('driver state at start:', {k: n.st.get(k) for k in ('enabled', 'estop', 'plugged')}, flush=True)
    if n.st.get('estop'):
        n.reset(); n.spin(0.5)
    try:
        if mode == 'characterize':
            characterize(n)
        else:
            prove(n, sys.argv[2].split(',') if len(sys.argv) > 2 else None)
    finally:
        n.pub.publish(Twist())
        n.enable(False)
        n.spin(0.3)
        print('end state:', {k: n.st.get(k) for k in ('enabled', 'estop')})


if __name__ == '__main__':
    main()
