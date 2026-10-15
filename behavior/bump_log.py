"""Log-only contact recorder (bump tests, 2026-09-29). Commands nothing: the owner drives with the gamepad.
Records at full rate: IMU (/imu/data_raw ~110 Hz), commanded body velocity (/wheel/odom = gated COMMAND, no encoders),
LiDAR odometry (/odom_rf2o 10 Hz: twist + pose), LiDAR range along base +x/+y/-x/-y, driver state (enabled / e-stop / pad mode). A contact should show as commanded
speed with no measured speed (stall) plus an IMU jolt; this run collects both to set the thresholds.
Output ~/bump/<UTC>/log.npz. Usage (robot, ROS sourced): python bump_log.py [--seconds 300]"""
import argparse, json, os, time

import numpy as np
import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu, LaserScan
import math
from std_msgs.msg import String


class Log(Node):
    def __init__(self):
        super().__init__('bump_log')
        self.imu, self.cmd, self.meas, self.st = [], [], [], []
        now = lambda: self.get_clock().now().nanoseconds * 1e-9
        self.create_subscription(Imu, '/imu/data_raw', lambda m: self.imu.append(
            (now(), m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z,
             m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z)), 200)
        tw = lambda m: (now(), m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z)
        self.create_subscription(Odometry, '/wheel/odom', lambda m: self.cmd.append(tw(m)), 100)
        def on_rf2o(m):
            q = m.pose.pose.orientation
            self.meas.append(tw(m) + (m.pose.pose.position.x, m.pose.pose.position.y,
                                      math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))))
        self.create_subscription(Odometry, '/odom_rf2o', on_rf2o, 50)
        self.rng = []
        def on_scan(m):   # median range in +-5 deg around base_link +x, +y, -x, -y (laser yaw pi per TF)
            ang = m.angle_min + np.arange(len(m.ranges)) * m.angle_increment + math.pi
            r = np.array(m.ranges); ok = np.isfinite(r) & (r > m.range_min)
            out = [now()]
            for h in (0, math.pi / 2, math.pi, -math.pi / 2):
                sel = ok & (np.abs(np.angle(np.exp(1j * (ang - h)))) < math.radians(5))
                out.append(float(np.median(r[sel])) if sel.any() else float('nan'))
            self.rng.append(tuple(out))
        self.create_subscription(LaserScan, '/scan', on_scan, 5)
        def on_state(m):
            s = json.loads(m.data)
            self.st.append((now(), float(bool(s.get('enabled'))), float(s.get('estop') is not None),
                            {'locked': 0.0, 'drive': 1.0, 'arm': 2.0}.get(s.get('pad_mode'), -1.0)))
        self.create_subscription(String, '/rosorin_board_driver/state', on_state, 20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=300)
    a = ap.parse_args()
    out = os.path.expanduser(time.strftime('~/bump/%Y%m%dT%H%M%SZ', time.gmtime()))
    os.makedirs(out)
    rclpy.init(); n = Log()
    t_end, t_print = time.time() + a.seconds, 0
    try:
        while time.time() < t_end:
            rclpy.spin_once(n, timeout_sec=0.05)
            if time.time() - t_print > 10:
                t_print = time.time()
                print(f'{int(t_end - time.time())} s left: imu {len(n.imu)} cmd {len(n.cmd)} rf2o {len(n.meas)}', flush=True)
    finally:
        np.savez(os.path.join(out, 'log.npz'), imu=np.array(n.imu), cmd=np.array(n.cmd), meas=np.array(n.meas), rng=np.array(n.rng),
                 state=np.array(n.st))
        print('saved', out, flush=True)
        n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
