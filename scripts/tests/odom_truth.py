#!/usr/bin/env python3
"""Which odometry is right? Floor test with a ground truth that needs no odometry:
  straight drive  -> truth = change of the LiDAR range to whatever is straight ahead (ray along the motion)
  strafe          -> truth = change of the LiDAR range straight to that side
  turn in place   -> truth = gyro z integrated (bias taken while still)
Compared: /odom_vslam (camera, cuVSLAM), /odom_rf2o (LiDAR odometry), /odometry/filtered (EKF = commanded velocity).
Forward first into a LiDAR-clear corridor, back over the same floor. Harness guards as in stop_proof_floor.py."""
import math, os, sys, time

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stop_proof_floor import Rig, CLEAR, GUARD  # noqa: E402
from nav_msgs.msg import Odometry  # noqa: E402


def pose(m):
    q, p = m.pose.pose.orientation, m.pose.pose.position
    return time.time(), p.x, p.y, math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def ray(n, bearing_deg, half=4.0):
    """Median LiDAR distance along a base_link bearing (deg), or None."""
    x, y, ok = n.points()
    b = np.degrees(np.arctan2(y, x))
    sel = ok & (np.abs(((b - bearing_deg + 180) % 360) - 180) < half)
    return float(np.median(np.hypot(x[sel], y[sel]))) if sel.sum() >= 3 else None


def main():
    rclpy.init()
    n = Rig()
    logs = {'vslam': [], 'rf2o': [], 'ekf': []}
    n.create_subscription(Odometry, 'odom_vslam', lambda m: logs['vslam'].append(pose(m)), 50)
    n.create_subscription(Odometry, 'odom_rf2o', lambda m: logs['rf2o'].append(pose(m)), 50)
    n.create_subscription(Odometry, 'odometry/filtered', lambda m: logs['ekf'].append(pose(m)), 50)
    t = time.time()
    while (not n.st or n.scan is None) and time.time() - t < 15:
        n.spin(0.5)
    n.spin(2.0)
    if not n.st or n.scan is None or n.st.get('plugged'):
        print('ABORT: no driver state / LiDAR, or plugged in'); return
    n.drive_mode()
    if n.st.get('estop'):
        n.reset(); n.spin(0.3)
    if not n.enable(True):
        print('enable refused', n.st); return
    n.spin(5.0)

    def last(name):
        return logs[name][-1] if logs[name] else None

    def still_bias(s=1.5):
        t0 = time.time(); n.spin(s)
        g = [v for tt, v in n.gz if tt >= t0]
        return float(np.mean(g)) if g else 0.0

    def leg(name, s, vx=0.0, vy=0.0, wz=0.0, bearing=None):
        sign = 1 if vx >= 0 else -1
        if vx > 0:
            near, valid = n.corridor(1)
            if (near is not None and near < CLEAR + abs(vx) * s) or valid < 0.6:
                print(f'{name}: NOT RUN - corridor ahead nearest {near}, valid {valid:.0%}'); return False
        bias = still_bias()
        r0 = ray(n, bearing) if bearing is not None else None
        p0 = {k: last(k) for k in logs}
        m = Twist(); m.linear.x, m.linear.y, m.angular.z = float(vx), float(vy), float(wz)
        t0 = time.time()
        while time.time() - t0 < s:
            n.pub.publish(m); n.spin(0.05)
            if vx:
                near, _ = n.corridor(sign)
                if near is not None and near < GUARD:
                    n.stop_all(); print(f'{name}: ABORTED - something within {GUARD} m'); return False
        n.pub.publish(Twist()); n.spin(1.5)
        t1 = time.time()
        gyro = sum((b[1] - bias) * (b[0] - a[0]) for a, b in zip(n.gz, n.gz[1:]) if t0 <= b[0] <= t1)
        r1 = ray(n, bearing) if bearing is not None else None
        truth = None if r0 is None or r1 is None else r0 - r1
        out = [f'{name:16s}']
        if wz:
            out.append(f'truth(gyro) {math.degrees(gyro):+7.1f} deg |')
        else:
            out.append(f'truth(LiDAR ray) {"  n/a" if truth is None else f"{100 * truth:+6.1f}"} cm, turned {math.degrees(gyro):+.1f} deg |')
        for k in logs:
            a, b = p0[k], last(k)
            if a is None or b is None or b[0] < t0:
                out.append(f'{k} n/a'); continue
            d = math.hypot(b[1] - a[1], b[2] - a[2])
            dy = math.degrees(math.atan2(math.sin(b[3] - a[3]), math.cos(b[3] - a[3])))
            out.append(f'{k} {dy:+7.1f} deg {100 * d:5.1f} cm' if wz else f'{k} {100 * d:6.1f} cm')
        print('  '.join(out), flush=True)
        return True

    try:
        if len(sys.argv) > 1 and sys.argv[1] == 'strafe':     # sideways + sideways-while-turning (truth: LiDAR ray to the side)
            leg('strafe left 0.15', 3.0, vy=0.15, bearing=90)
            leg('strafe right 0.15', 3.0, vy=-0.15, bearing=90)
            leg('left + turn', 3.0, vy=0.15, wz=0.37)
            leg('right + turn', 3.0, vy=-0.15, wz=-0.37)
            return
        if len(sys.argv) > 1 and sys.argv[1] == 'one':
            secs = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
            if leg('forward 0.2', secs, vx=0.2, bearing=0):
                n.spin(3.0); leg('back 0.2', secs, vx=-0.2, bearing=0)
            return
        for rep in range(2):
            if leg('forward 0.2', 4.0, vx=0.2, bearing=0):
                leg('back 0.2', 4.0, vx=-0.2, bearing=0)
        if leg('forward 0.35', 3.0, vx=0.35, bearing=0):
            leg('back 0.35', 3.0, vx=-0.35, bearing=0)
        leg('turn +0.6', 3.0, wz=0.6)
        leg('turn -0.6', 3.0, wz=-0.6)
        leg('turn +1.0', 2.0, wz=1.0)
        leg('turn -1.0', 2.0, wz=-1.0)
    finally:
        n.pub.publish(Twist()); n.enable(False); n.spin(0.3)
        print('end state:', {k: n.st.get(k) for k in ('enabled', 'estop')}, 'battery', n.bat)


if __name__ == '__main__':
    main()
