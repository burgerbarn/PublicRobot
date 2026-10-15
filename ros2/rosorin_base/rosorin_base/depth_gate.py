"""Depth for nvblox only while the head (arm) is still.

Why (measured 2026-10-02, bag 20261002_121621, scripts/tests/nvblox_*): the camera sits on the arm and its TF comes
from /joint_states. While the arm moves, the TF does not match the image (a single slow move blocks the driver: no
joint states at all until it ends; streamed moves report the command, the servos lag behind). nvblox integrated
those frames: in the second the arm went to its drive pose the obstacle slice jumped from 330 to 1244 cells and put
73 obstacle cells on plain rug 0.5-1.7 m straight ahead; stock decay needed ~12 s to remove them, the robot stood
still for 8 s. nvblox has no input gate of its own, so this node is the gate: it republishes the depth image
(serialized, untouched) on ~/depth only when the arm has been still for settle_s.

A depth frame with stamp T passes when
  - joint states are fresh at T (the newest one is at most stale_s older than T) - a blocking move silences them;
  - no joint moved more than eps within settle_s before T;
  - the driver does not say arm_busy (set around its blocking moves) and has not for settle_s.
Custom code because neither nvblox nor Nav2 has this; vslam_odom does the same for the camera odometry.
Note: with wheels AND arm disabled the driver re-reads the servos every 2 s (joint states pause ~0.5 s), so an idle
robot loses some frames here; nvblox only runs inside navigation, where the wheels are enabled and nothing is read."""
import struct

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import Bool


class DepthGate(Node):
    def __init__(self):
        super().__init__('depth_gate')
        dp = self.declare_parameter
        self.settle = dp('settle_s', 0.6).value
        self.stale = dp('stale_s', 0.25).value          # joint states come at 10 Hz
        self.eps = dp('joint_eps_rad', 0.002).value
        self.last_pos, self.t_joint, self.t_change = None, None, None
        self.busy, self.t_busy_end = False, None
        self.passed = self.dropped = 0
        self.why = {}
        self.pub = self.create_publisher(Image, '~/depth', 5)        # reliable: matches any subscriber QoS (nvblox)
        self.create_subscription(JointState, 'joint_states', self.on_joints, 50)
        latched = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE,
                             durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Bool, 'rosorin_board_driver/arm_busy', self.on_busy, latched)
        self.create_subscription(Image, 'depth_in', self.on_depth, qos_profile_sensor_data, raw=True)
        self.create_timer(30.0, self.report)

    def now(self):
        return self.get_clock().now().nanoseconds * 1e-9

    def on_joints(self, m):
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        pos = dict(zip(m.name, m.position))
        if self.last_pos is not None and any(abs(pos.get(k, v) - v) > self.eps for k, v in self.last_pos.items()):
            self.t_change = t
        if self.last_pos is None or (self.t_joint is not None and t - self.t_joint > self.stale):
            self.t_change = t                           # first message, or back after a silence: settle first
        self.last_pos, self.t_joint = pos, t

    def on_busy(self, m):
        if self.busy and not m.data:
            self.t_busy_end = self.now()
        self.busy = m.data

    def reason(self, t):
        if self.t_joint is None:
            return 'no joint states yet'
        if t - self.t_joint > self.stale:
            return 'joint states silent (blocking arm move)'
        if self.t_change is not None and t - self.t_change < self.settle:
            return 'arm moved'
        if self.busy:
            return 'driver: arm busy'
        if self.t_busy_end is not None and self.now() - self.t_busy_end < self.settle:
            return 'arm busy just ended'
        return None

    def on_depth(self, raw):
        sec, nsec = struct.unpack_from('<iI', raw, 4)   # CDR: 4-byte encapsulation header, then header.stamp
        why = self.reason(sec + nsec * 1e-9)
        if why is None:
            self.passed += 1
            self.pub.publish(raw)
        else:
            self.dropped += 1
            self.why[why] = self.why.get(why, 0) + 1

    def report(self):
        if self.dropped:
            self.get_logger().info(f'depth frames: {self.passed} passed, {self.dropped} held back {self.why}')
        self.passed = self.dropped = 0
        self.why = {}


def main():
    rclpy.init()
    n = DepthGate()
    try:
        rclpy.spin(n)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        n.report()
        n.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
