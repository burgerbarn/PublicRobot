"""Character demo (roadmap #7): "look toward the TV" plain vs Wall-E style, arm only (wheels disabled).
Same interface and safety as vision/scan.py: goals on `arm/goal` (the driver enforces joint limits - refuse, not
clamp - the 50 p/s cap, e-stop, auto-tuck). A step is done when the driver reports the commanded pose == goal.

Poses (default pose 1:500 2:860 3:45 4:178 5:500): TV at about -19 deg -> pan (joint 1) ~417 (0.24 deg/pulse,
lower pulse = to the right, from scan TF). Head tilt = wrist roll (joint 5) +-30. Collision model flags ANY joint-5
roll against the camera mount; that is a known false positive: link5 and the camera mount are both on link4, so their
relation depends on joint 5 only, and joint 5 swept its full range 11..989 with the owner watching (motion_safety.md).

Run on the robot after `~/arm/recover`:  python3 look_demo.py plain|walle
Ends back in the default pose, arm disabled (holding) -> auto-tuck after 2 min."""
import json, sys, time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool

DRV = '/rosorin_board_driver'
HOME = {1: 500, 4: 178, 5: 500}
TV = 417
# (goal, pause after in s). Wall-E: anticipation, hesitation, overshoot + settle, curious tilt, back to level.
STYLES = {
    'plain': [({1: TV}, 1.5), (HOME, 0.5)],
    'walle': [({1: 508}, 0.0),                    # anticipation: tiny pullback the other way
              ({1: 500}, 0.6),                    # hesitation
              ({1: TV - 7}, 0.0),                 # overshoot...
              ({1: TV}, 0.4),                     # ...and settle
              ({4: 195, 5: 530}, 0.9),            # curious head tilt, a little lift
              ({5: 470}, 0.7),                    # tilt the other way
              ({4: 178, 5: 500}, 0.8),            # level again, keep looking
              (HOME, 0.5)],
}


class Demo(Node):
    def __init__(self):
        super().__init__('look_demo')
        self.state = None
        self.create_subscription(String, f'{DRV}/state', lambda m: setattr(self, 'state', json.loads(m.data)), 5)
        self.pub = self.create_publisher(JointState, 'arm/goal', 10)
        self.enable = self.create_client(SetBool, f'{DRV}/arm/enable')

    def spin_until(self, cond, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.05)
            if cond():
                return
        raise RuntimeError(f'timeout: {what}')

    def call_enable(self, on):
        self.enable.wait_for_service(timeout_sec=5)
        f = self.enable.call_async(SetBool.Request(data=on))
        self.spin_until(f.done, 20, 'arm/enable')
        return f.result()

    def go(self, goal):
        self.pub.publish(JointState(name=[str(k) for k in goal], position=[float(v) for v in goal.values()]))
        def arrived():
            s = self.state or {}
            p = s.get('arm_pose') or {}
            return not s.get('arm_moving') and all(abs(p.get(str(k), -999) - v) <= 1 for k, v in goal.items())
        self.spin_until(lambda: arrived() or (self.state or {}).get('estop'), 30, f'goal {goal}')
        if self.state.get('estop'):
            raise RuntimeError(f'e-stop: {self.state["estop"]}')


def main():
    style = sys.argv[1] if len(sys.argv) > 1 else 'walle'
    rclpy.init()
    n = Demo()
    n.spin_until(lambda: n.state is not None, 10, 'driver state')
    if n.state['enabled'] or n.state['estop']:
        raise SystemExit(f'refusing: wheels enabled or e-stop: {n.state}')
    r = n.call_enable(True)
    print('arm enable:', r.message)
    if not r.success:
        raise SystemExit('arm enable refused (run ~/arm/recover first)')
    n.spin_until(lambda: n.state.get('arm_enabled'), 5, 'arm enabled in state')
    t0 = time.time()
    try:
        for goal, pause in STYLES[style]:
            n.go(goal)
            end = time.time() + pause
            while time.time() < end:
                rclpy.spin_once(n, timeout_sec=0.05)
        print(f'{style}: done in {time.time() - t0:.1f} s')
    finally:
        try:
            n.go(HOME)
        finally:
            print('arm disable:', n.call_enable(False).message)
    n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
