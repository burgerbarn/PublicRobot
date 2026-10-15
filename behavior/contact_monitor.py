"""Contact (collision/stall) monitor. --act (owner 2026-10-02: "wheels moving but the LiDAR/camera see you're not moving
= you crashed -> stop"): on CONTACT it latches the board_driver e-stop (/estop). Without --act: log only.
No wheel encoders (board protocol), so contact = commanded motion that the LiDAR does not see happen.

Measured body velocity: differentiated rf2o POSE (x, y, yaw) over WIN s, rotated into the body frame. Not the rf2o
twist: it carries forward speed only and is averaged (rf2o CLaserOdometry2DNode.cpp:223, CLaserOdometry2D.cpp:988),
so strafe pushes were invisible in bump test 1 (2026-09-29).
Commanded: /wheel/odom (the driver's gated COMMAND), delayed by LAG (rf2o lag measured 0.2 s), averaged over WIN.
Detection: |cmd| >= V_MIN for at least SETTLE s (skips start-up/deadband), the part of the measured velocity along the
command < RATIO x |cmd|, for HOLD s -> CONTACT. Bump test 1: 3 forward hits + front push caught, cmd 0.08-0.23 m/s,
measured ~0.00.
Also logged per rf2o tick: LiDAR minimum range in the travel direction (+-30 deg), IMU deviation.
2026-10-02: rf2o does NOT see sideways motion (floor test: 15 cm strafe = 1.1 cm in rf2o, 15.4 cm in the camera
odometry) - it e-stopped a freely moving robot on every sideways move. Now the owner's rule as stated: wheels moving
and BOTH the LiDAR (rf2o) and the camera (/odom_vslam, cuVSLAM) see no motion -> contact. Camera odometry not fresh
(head moving, tracking lost): rf2o alone, and only for forward/backward-dominant commands (sideways it cannot judge).
Output ~/bump/<UTC>/monitor.npz + events.json + a camera frame per contact. Usage (robot, ROS sourced): python contact_monitor.py [--seconds 600]"""
import argparse, json, math, os, time
from collections import deque

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Image, Imu, LaserScan
from std_msgs.msg import Bool
import cv2

# RATIO 0.15: on the rug free driving makes only 25-88 % of the command (median 54 %, run 2); contacts ~0-3 %
LAG, WIN, V_MIN, SETTLE, RATIO, HOLD = 0.2, 0.3, 0.08, 0.5, 0.15, 0.3
LASER_YAW, LASER_X = math.pi, 0.048        # base_link -> laser (TF 2026-09-29: xyzw 0,0,1,0; x 0.048)


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class Monitor(Node):
    def __init__(self):
        super().__init__('contact_monitor')
        self.cmd = deque(maxlen=400)            # (t, vx, vy, wz)
        self.pose = deque(maxlen=50)            # (t, x, y, yaw)
        self.vpose = deque(maxlen=80)           # camera odometry (t, x, y, yaw)
        self.scan, self.imu_dev = None, 0.0
        self.g = None
        self.rows, self.events = [], []
        self.moving_since, self.stall_since, self.in_contact = None, None, False
        now = lambda: self.get_clock().now().nanoseconds * 1e-9
        self.now = now
        self.create_subscription(Odometry, '/wheel/odom', lambda m: self.cmd.append(
            (now(), m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z)), 100)
        self.create_subscription(Odometry, '/odom_rf2o', self.on_rf2o, 50)
        self.create_subscription(Odometry, '/odom_vslam', lambda m: self.vpose.append(
            (now(), m.pose.pose.position.x, m.pose.pose.position.y, yaw_of(m.pose.pose.orientation))), 50)
        self.create_subscription(LaserScan, '/scan', lambda m: setattr(self, 'scan', m), 5)
        self.create_subscription(Imu, '/imu/data_raw', self.on_imu, 200)
        self.rgb, self.out = None, None
        self.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: setattr(self, 'rgb', m), 2)
        self.act, self.estop_pub = False, self.create_publisher(Bool, '/estop', 10)

    def on_imu(self, m):
        a = np.array([m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z])
        if self.g is None:
            self.g = a
        self.imu_dev = max(self.imu_dev, float(np.linalg.norm(a - self.g)))

    def cmd_avg(self, t0, t1):
        c = [r for r in self.cmd if t0 <= r[0] <= t1]
        return np.mean([r[1:] for r in c], 0) if c else np.zeros(3)

    def ahead_range(self, vx, vy):
        s = self.scan
        if s is None or math.hypot(vx, vy) < 1e-3:
            return float('nan')
        heading = math.atan2(vy, vx)                               # travel direction in base_link
        ang = s.angle_min + np.arange(len(s.ranges)) * s.angle_increment + LASER_YAW
        r = np.array(s.ranges)
        ok = np.isfinite(r) & (r > s.range_min)
        d = np.angle(np.exp(1j * (ang - heading)))
        sel = ok & (np.abs(d) < math.radians(30))
        return float(r[sel].min()) if sel.any() else float('nan')

    def cam_velocity(self, t):
        """Body-frame velocity from the camera odometry over the last WIN s, or None if it is not fresh."""
        if not self.vpose or t - self.vpose[-1][0] > 0.4:
            return None
        old = [q for q in self.vpose if q[0] <= self.vpose[-1][0] - WIN + 0.02]
        if not old or self.vpose[-1][0] - old[-1][0] > 2 * WIN:
            return None
        q0, q1 = old[-1], self.vpose[-1]; dt = q1[0] - q0[0]
        dx, dy = (q1[1] - q0[1]) / dt, (q1[2] - q0[2]) / dt
        return math.cos(q0[3]) * dx + math.sin(q0[3]) * dy, -math.sin(q0[3]) * dx + math.cos(q0[3]) * dy

    def on_rf2o(self, m):
        t = self.now()
        p = m.pose.pose
        self.pose.append((t, p.position.x, p.position.y, yaw_of(p.orientation)))
        old = [q for q in self.pose if q[0] <= t - WIN + 0.02]
        if not old:
            return
        q0 = old[-1]; dt = t - q0[0]
        dx, dy = (p.position.x - q0[1]) / dt, (p.position.y - q0[2]) / dt
        y0 = q0[3]
        mvx, mvy = math.cos(y0) * dx + math.sin(y0) * dy, -math.sin(y0) * dx + math.cos(y0) * dy
        # (2026-09-30: the sign flip added after the 2026-09-29 direction test was WRONG per the robot's own self-check
        # on the floor - raw rf2o pose sign is right. Removed.)
        mwz = math.atan2(math.sin(self.pose[-1][3] - y0), math.cos(self.pose[-1][3] - y0)) / dt
        cvx, cvy, cwz = self.cmd_avg(q0[0] - LAG, t - LAG)
        cl = math.hypot(cvx, cvy)
        along = (mvx * cvx + mvy * cvy) / cl if cl > 1e-3 else 0.0
        if cl >= V_MIN:
            self.moving_since = self.moving_since or t
        else:
            self.moving_since = None
        settled = self.moving_since is not None and t - self.moving_since >= SETTLE
        cam = self.cam_velocity(t)
        along_cam = None if cam is None or cl <= 1e-3 else (cam[0] * cvx + cam[1] * cvy) / cl
        if along_cam is not None:                              # both must see no motion
            stall = settled and along < RATIO * cl and along_cam < RATIO * cl
        else:                                                  # LiDAR alone: it cannot judge sideways motion
            stall = settled and along < RATIO * cl and abs(cvx) >= abs(cvy)
        self.stall_since = (self.stall_since or t) if stall else None
        contact = self.stall_since is not None and t - self.stall_since >= HOLD
        rng = self.ahead_range(cvx, cvy)
        if contact and not self.in_contact:
            ev = {'t': t, 'cmd': [round(cvx, 3), round(cvy, 3), round(cwz, 2)], 'meas': [round(mvx, 3), round(mvy, 3), round(mwz, 2)],
                  'along': round(along, 3), 'along_camera': None if along_cam is None else round(along_cam, 3), 'ahead_range_m': round(rng, 3), 'imu_dev': round(self.imu_dev, 2)}
            self.events.append(ev)
            if self.rgb is not None and self.out:                  # what the camera saw at the moment of contact
                m_ = self.rgb
                img = np.frombuffer(m_.data, np.uint8).reshape(m_.height, m_.width, -1)
                img = img if m_.encoding == 'bgr8' else img[:, :, ::-1]
                ev['frame'] = f'contact_{len(self.events):02d}.jpg'
                cv2.imwrite(os.path.join(self.out, ev['frame']), img)
            if self.act:                                           # crashed into something: stop, now
                for _ in range(3):
                    self.estop_pub.publish(Bool(data=True))
            print(f'{time.strftime("%H:%M:%S")} CONTACT{" -> E-STOP" if self.act else " (log only)"}: cmd vx {cvx:+.2f} vy {cvy:+.2f} wz {cwz:+.2f} | '
                  f'measured along: LiDAR {along:+.3f}, camera {"n/a" if along_cam is None else format(along_cam, "+.3f")} m/s | {rng:.2f} m to nearest LiDAR return ahead', flush=True)
        if not contact and self.in_contact:
            print(f'{time.strftime("%H:%M:%S")}   contact over', flush=True)
        self.in_contact = contact
        self.rows.append((t, cvx, cvy, cwz, mvx, mvy, mwz, along, rng, self.imu_dev, float(contact),
                          float('nan') if along_cam is None else along_cam))
        self.imu_dev = 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=600)
    ap.add_argument('--act', action='store_true', help='latch the e-stop on contact')
    a = ap.parse_args()
    out = os.path.expanduser(time.strftime('~/bump/%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out)
    rclpy.init(); n = Monitor(); n.out = out; n.act = a.act
    print(f'contact monitor running ({"ACT: e-stop on contact" if a.act else "log only"})', flush=True)
    t_end = time.time() + a.seconds
    try:
        while time.time() < t_end:
            rclpy.spin_once(n, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        np.savez(os.path.join(out, 'monitor.npz'), rows=np.array(n.rows))
        json.dump(n.events, open(os.path.join(out, 'events.json'), 'w'), indent=1)
        print(f'saved {out}: {len(n.events)} contacts', flush=True)
        n.destroy_node(); rclpy.try_shutdown()      # SIGINT (systemctl stop): rclpy's handler already shut down


if __name__ == '__main__':
    main()
