"""Robot self-check of its odometry (run by the robot before trusting navigation; safe, tiny move on the rug).
Needs >= CLEAR m free ahead and behind on the LiDAR (+-20 deg). Moves forward 0.15 m/s for T s, pauses, back the
same. Reference = change of the LiDAR distance to whatever is straight ahead (median over +-3 deg); compares it with
the EKF (/odometry/filtered, now fed by odom_lidar) and the raw odom_lidar displacement. Pass = sign right and
|error| < 30 %. Pauses the robot mind for the wheels (it yields anyway). Writes ~/behavior/selfcheck/<UTC>.json."""
import json, math, os, sys, time

import numpy as np
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import String
from std_srvs.srv import SetBool

sys.path.insert(0, os.path.expanduser('~/vision'))
import numpy as np  # noqa: E402 (floor check)
from mind_pause import mind_paused

FLOOR_MAX = 0.05                      # m: floor plane below base_link; stand ~0.09, floor ~0.02 (measured)
CLEAR, V, T = 0.8, 0.2, 2.0         # 0.15 m/s x 1.2 s did not move it on the rug (2026-09-30)
LASER_YAW = math.pi                     # TF base_link->laser (verified against depth 2026-09-30)


def sector(scan, center_deg, half_deg):
    ang = scan.angle_min + np.arange(len(scan.ranges)) * scan.angle_increment + LASER_YAW
    r = np.array(scan.ranges)
    d = np.degrees(np.angle(np.exp(1j * (ang - math.radians(center_deg)))))
    ok = (np.abs(d) < half_deg) & np.isfinite(r) & (r > 0.05)
    return r[ok]


def main():
    out = os.path.expanduser(time.strftime('~/behavior/selfcheck/%Y%m%dT%H%M%SZ.json', time.gmtime()))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with mind_paused('odometry self-check'):
        rclpy.init(); n = rclpy.create_node('odom_selfcheck'); last = {}
        n.create_subscription(LaserScan, '/scan', lambda m: last.__setitem__('scan', m), 5)
        n.create_subscription(Odometry, '/odometry/filtered', lambda m: last.__setitem__('ekf', m), 20)
        n.create_subscription(Odometry, '/odom_lidar', lambda m: last.__setitem__('lid', m), 20)
        n.create_subscription(Odometry, '/odom_rf2o', lambda m: last.__setitem__('raw', m), 20)
        n.create_subscription(String, '/rosorin_board_driver/state', lambda m: last.__setitem__('st', json.loads(m.data)), 5)
        pub = n.create_publisher(Twist, 'cmd_vel', 10)
        imu = []
        n.create_subscription(Imu, '/imu/data_raw', lambda m: imu.append((time.time(), m.linear_acceleration.x, m.linear_acceleration.y, m.angular_velocity.z)), 200)
        en = n.create_client(SetBool, '/rosorin_board_driver/enable')

        def spin(t):
            end = time.time() + t
            while time.time() < end:
                rclpy.spin_once(n, timeout_sec=0.02)

        def enable(on):
            en.wait_for_service(timeout_sec=5); f = en.call_async(SetBool.Request(data=on))
            rclpy.spin_until_future_complete(n, f, timeout_sec=20); return f.result()

        spin(2.0)
        st = last.get('st', {})
        if st.get('estop') or st.get('pad_mode') not in ('locked', None) or n.count_publishers('cmd_vel') > 1:   # >1: ours is one of them
            raise SystemExit(f'not safe to move: estop {st.get("estop")}, pad {st.get("pad_mode")}, cmd_vel pubs {n.count_publishers("cmd_vel")}')
        # ON THE FLOOR? (2026-09-30: a run happened on the stand, plugged in, wheels in the air.) Depth floor plane:
        # ~0.02 m below base_link on the floor, ~0.09 m on the stand (measured). Refuse above FLOOR_MAX.
        from floor_check import cloud_xyz, fit_floor
        from scan import quat_to_R
        from sensor_msgs.msg import PointCloud2
        from tf2_ros import Buffer, TransformListener
        from rclpy.time import Time
        tfb = Buffer(); TransformListener(tfb, n)
        from std_srvs.srv import Trigger
        dc = n.create_client(Trigger, '/rosorin_board_driver/arm/drive'); dc.wait_for_service(timeout_sec=5)
        fdr = dc.call_async(Trigger.Request()); rclpy.spin_until_future_complete(n, fdr, timeout_sec=60)   # camera -29 deg at the floor
        n.create_subscription(PointCloud2, '/aurora/points2', lambda m: last.__setitem__('cloud', m), 2)
        spin(2.0)
        cm = last.get('cloud')
        if cm is None:
            raise SystemExit('no depth cloud: cannot confirm the robot is on the floor')
        tf = tfb.lookup_transform('base_link', cm.header.frame_id, Time())
        R = quat_to_R(tf.transform.rotation); tr = np.array([getattr(tf.transform.translation, k) for k in 'xyz'])
        xyz, okm = cloud_xyz(cm); P = xyz[okm] @ R.T + tr
        P = P[(P[:, 0] > 0.3) & (P[:, 0] < 2.0) & (np.abs(P[:, 1]) < 1.0)]
        _, below, _ = fit_floor(P[::max(1, len(P) // 20000)], np.random.default_rng(0))
        res0 = {'floor_below_base_m': round(float(below), 3)}
        if not 0.0 <= below <= FLOOR_MAX:                    # 2026-09-30: a ceiling fit gave -2.38 and slipped through
            raise SystemExit(f'floor {below:.3f} m below base_link (want 0..{FLOOR_MAX}): not on the floor / no floor seen - not driving')
        # forward only: the LiDAR returns NOTHING in the rear +-20 deg (2026-09-30) - never reverse into unseen space
        front = sector(last['scan'], 0, 20)
        if front.size == 0 or front.min() < CLEAR:
            raise SystemExit(f'not enough room ahead: {front.min() if front.size else None}')

        def yaw(m):
            q = m.pose.pose.orientation
            return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))

        def snapshot():
            e, l, w = last['ekf'].pose.pose.position, last['lid'].pose.pose.position, last['raw'].pose.pose.position
            return {'ahead': float(np.median(sector(last['scan'], 0, 3))), 'ekf': (e.x, e.y, yaw(last['ekf'])),
                    'lidar': (l.x, l.y, yaw(last['lid'])), 'raw': (w.x, w.y, yaw(last['raw']))}

        def along(a, b):                                       # displacement projected on the START heading (+ = forward)
            return (b[0] - a[0]) * math.cos(a[2]) + (b[1] - a[1]) * math.sin(a[2])

        r = enable(True)
        if not r.success:
            raise SystemExit('wheels enable refused: ' + r.message)
        res = {'v': V, 't': T, 'moves': [], **res0}
        try:
            for sign in (1,):
                spin(1.0); a = snapshot()
                end = time.time() + T
                while time.time() < end:
                    pub.publish(Twist(linear=Twist().linear.__class__(x=sign * V)))
                    rclpy.spin_once(n, timeout_sec=0.02)
                    if last.get('st', {}).get('estop'):
                        raise RuntimeError('e-stop')
                    res.setdefault('gate_vel', []).append(last.get('st', {}).get('body_vel'))
                for _ in range(5):
                    pub.publish(Twist()); time.sleep(0.03)
                spin(1.5); b = snapshot()
                ref = a['ahead'] - b['ahead']                          # moved toward the thing ahead = +
                ekf, lid, raw = along(a['ekf'], b['ekf']), along(a['lidar'], b['lidar']), along(a['raw'], b['raw'])
                res['moves'].append({'cmd': 'forward' if sign > 0 else 'back', 'lidar_range_change_m': round(ref, 3),
                                     'ekf_m': round(ekf, 3), 'odom_lidar_m': round(lid, 3),
                                     'raw_rf2o_m': round(raw, 3), 'yaw_start_deg': round(math.degrees(a['raw'][2]), 1)})
        finally:
            for _ in range(5):
                pub.publish(Twist()); time.sleep(0.03)
            enable(False)
        ok = all(abs(m['lidar_range_change_m']) > 0.02 and
                 abs(m['odom_lidar_m'] - m['lidar_range_change_m']) < 0.3 * abs(m['lidar_range_change_m']) + 0.02
                 for m in res['moves'])
        res['pass'] = ok
        I = np.array(imu); res['imu_ax_std_during_run'] = round(float(I[:, 1].std()), 3) if len(I) else None; res['imu_n'] = len(I)
        g = [v[0] for v in res.pop('gate_vel', []) if v]
        res['gate_vx_max'] = max(g) if g else None
        json.dump(res, open(out, 'w'), indent=1)
        print(json.dumps(res), flush=True)
        n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
