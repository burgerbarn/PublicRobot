"""Baseline driving for the contact detector (no contact): scripted cmd_vel while a rosbag records IMU, rf2o LiDAR odometry,
commanded velocity, battery and driver state. Owner present with the gamepad (any button = e-stop). Wheels enabled only
for the run, disabled at the end (also on Ctrl-C / error). This must be the ONLY cmd_vel publisher (driver trips on >1).
Sequence (m/s, rad/s, s): fwd 0.1 x2, back 0.1 x2, fwd 0.2 x1.5, back 0.2 x1.5, turn +0.5 x2, turn -0.5 x2, 1.5 s stops.
Needs ~0.5 m clear in front and behind. Run: python3 drive_baseline.py (with rf2o + base running)."""
import json, time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool

DRV = '/rosorin_board_driver'
SEQ = [(0, 0, 1.0), (0.1, 0, 2.0), (0, 0, 1.5), (-0.1, 0, 2.0), (0, 0, 1.5), (0.2, 0, 1.5), (0, 0, 1.5),
       (-0.2, 0, 1.5), (0, 0, 1.5), (0, 0.5, 2.0), (0, 0, 1.5), (0, -0.5, 2.0), (0, 0, 1.5)]


def main():
    rclpy.init()
    n = Node('drive_baseline')
    state = {}
    n.create_subscription(String, f'{DRV}/state', lambda m: state.update(json.loads(m.data)), 5)
    pub = n.create_publisher(Twist, 'cmd_vel', 10)
    en = n.create_client(SetBool, f'{DRV}/enable')
    en.wait_for_service(timeout_sec=5)

    def enable(on):
        f = en.call_async(SetBool.Request(data=on))
        end = time.time() + 10
        while not f.done() and time.time() < end:
            rclpy.spin_once(n, timeout_sec=0.05)
        return f.result()

    end = time.time() + 5
    while not state and time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.05)
    if state.get('estop') or state.get('arm_enabled'):
        raise SystemExit(f'refusing: {state}')
    if n.count_publishers('cmd_vel') > 1:
        raise SystemExit('another cmd_vel publisher exists')
    r = enable(True)
    print('wheels:', r.message)
    if not r.success:
        raise SystemExit('enable refused')
    try:
        for vx, wz, dur in SEQ:
            print(f'cmd vx={vx:+.2f} wz={wz:+.2f} for {dur} s', flush=True)
            end = time.time() + dur
            while time.time() < end:
                pub.publish(Twist(linear=Twist().linear.__class__(x=float(vx)), angular=Twist().angular.__class__(z=float(wz))))
                rclpy.spin_once(n, timeout_sec=0.0)
                if state.get('estop'):
                    raise RuntimeError(f'e-stop: {state["estop"]}')
                time.sleep(0.05)
    finally:
        for _ in range(5):
            pub.publish(Twist()); time.sleep(0.05)
        print('wheels:', enable(False).message)
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
