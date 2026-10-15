"""Measured odometry from the depth camera: NVIDIA cuVSLAM (v17, RGB-D mode) on the Aurora's RGB image + depth
(the driver's align_mode registers depth to the RGB image). NOT the IR image: it carries the camera's own projected
dot pattern, which moves with the camera - cuVSLAM locks onto it and sees no motion (2026-10-02: 14 deg head turn
read as 0.5 deg).
The base has no wheel encoders (wheel/odom = commanded velocity); this is what the robot actually did.

cuVSLAM tracks the CAMERA (on the arm). Base pose = camera pose composed with the arm's TF at the image time:
  base motion per frame = (base->cam before) * (camera motion per cuVSLAM) * (base->cam now)^-1, integrated in the
floor plane (x, y, yaw). Publishes nav_msgs/Odometry on odom_vslam (frame odom_vslam,
child base_link; pose + twist in base_link). No TF. Tracking lost -> nothing published, tracker restarted (logged).
While the head moves the estimate is not trusted (2026-10-02: slow turns match the arm within 0.5 deg, but a fast
90 deg sweep left 0.5 m of false translation): steps taken while the head moves, and HEAD_SETTLE_S after, are not counted. A jump no robot motion can explain (MAX_V / MAX_W) is dropped and the pose re-anchored. Test: scripts/tests/cuvslam_rgbd_test.py."""
import math
import os

# numpy's OpenBLAS starts a pool of worker threads that spin (sched_yield) after every LAPACK call; np.linalg.inv per
# frame kept three of them spinning: 1.9 of 8 cores for 4x4 matrices (measured 2026-10-05 with gdb; cuVSLAM itself
# costs 0.16 core, scripts/tests/cuvslam_cpu_options.py). One thread = no pool. Must be set before numpy loads.
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')      # setdefault: the unit's environment can override it (self-care test)

import numpy as np
import rclpy
from message_filters import ApproximateTimeSynchronizer, Subscriber
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, Image
from tf2_ros import Buffer, TransformListener

import cuvslam as vslam

DEPTH_PER_M = 1000.0                        # depth image: mono16 millimetres
HEAD_MOVE = (0.003, math.radians(0.5))      # camera vs base change between frames (m, rad) that counts as a head move
HEAD_SETTLE_S = 0.4                         # wait this long after the head stops (arm TF lags the image)
MAX_V, MAX_W = 1.5, 4.0                     # m/s, rad/s of the base: beyond this the estimate jumped (base tops out far lower)
RESTART_AFTER = 15                          # consecutive failed frames (~1 s) -> new tracker


def quat_to_mat(x, y, z, w):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def to_T(R, p):
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = p
    return T


class VslamOdom(Node):
    def __init__(self):
        super().__init__('vslam_odom')
        self.info = None
        self.tracker = None
        self.prev = None                    # (t_ns, cuVSLAM world->camera, base->camera) of the previous frame
        self.x = self.y = self.yaw = 0.0    # base_link in odom_vslam (planar)
        self.last_t = None
        self.fails = self.restarts = self.ok = self.jumps = self.head_moves = 0
        self.head_until = 0                 # image time (ns) until which the head counts as moving
        self.buf = Buffer()
        self.tfl = TransformListener(self.buf, self)
        self.pub = self.create_publisher(Odometry, 'odom_vslam', 10)
        self.create_subscription(CameraInfo, '/aurora/rgb/camera_info', self.on_info, 1)
        ir = Subscriber(self, Image, '/aurora/rgb/image_raw', qos_profile=qos_profile_sensor_data)
        dp = Subscriber(self, Image, '/aurora/depth/image_raw', qos_profile=qos_profile_sensor_data)
        self.sync = ApproximateTimeSynchronizer([ir, dp], 10, 0.09)   # RGB is stamped ~70 ms after depth
        self.sync.registerCallback(self.on_frame)
        self.create_timer(60.0, self.report)
        self.async_sba = self.declare_parameter('async_sba', True).value     # cuVSLAM settings under test (CPU load, 2026-10-05)

    def on_info(self, m):
        if self.info is None and m.width:
            self.info = m

    def new_tracker(self):
        i = self.info
        cam = vslam.Camera()
        cam.distortion = (vslam.Distortion(vslam.Distortion.Model.Brown, list(i.d[:5]))
                          if i.distortion_model == 'plumb_bob' and len(i.d) >= 5
                          else vslam.Distortion(vslam.Distortion.Model.Pinhole))
        cam.focal = i.k[0], i.k[4]
        cam.principal = i.k[2], i.k[5]
        cam.size = i.width, i.height
        rgbd = vslam.Tracker.OdometryRGBDSettings()
        rgbd.depth_scale_factor = DEPTH_PER_M
        rgbd.depth_camera_id = 0
        rgbd.enable_depth_stereo_tracking = False
        cfg = vslam.Tracker.OdometryConfig(async_sba=self.async_sba, odometry_mode=vslam.Tracker.OdometryMode.RGBD,
                                           rgbd_settings=rgbd)
        self.tracker = vslam.Tracker(vslam.Rig([cam]), cfg)
        self.prev = None
        self.fails = 0

    def base_to_cam(self, stamp):
        """base_link -> camera at the image time (latest TF if that time is not in the buffer yet)."""
        for when in (Time.from_msg(stamp), Time()):
            try:
                t = self.buf.lookup_transform('base_link', self.info.header.frame_id, when).transform
                q, p = t.rotation, t.translation
                return to_T(quat_to_mat(q.x, q.y, q.z, q.w), [p.x, p.y, p.z])
            except Exception:
                continue
        return None

    def on_frame(self, ir, dp):
        if self.info is None:
            return
        t_ns = ir.header.stamp.sec * 1_000_000_000 + ir.header.stamp.nanosec
        if self.last_t is not None and t_ns <= self.last_t:
            return
        self.last_t = t_ns
        if self.tracker is None:
            self.new_tracker()
        T_bc = self.base_to_cam(ir.header.stamp)
        if T_bc is None:
            return
        img = np.ascontiguousarray(np.frombuffer(ir.data, np.uint8).reshape(ir.height, ir.width, -1)[:, :, :3])
        dep = np.ascontiguousarray(np.frombuffer(dp.data, np.uint16).reshape(dp.height, dp.step // 2)[:, :dp.width])
        try:
            est, _ = self.tracker.track(t_ns, images=[img], depths=[dep])
            wfr = est.world_from_rig
        except Exception as e:                                   # bad frame/timestamp: count as a failed frame
            self.get_logger().warn(f'track error: {e}', throttle_duration_sec=10.0)
            wfr = None
        if wfr is None:
            self.fails += 1
            if self.fails >= RESTART_AFTER:
                self.restarts += 1
                self.get_logger().warn(f'tracking lost for {self.fails} frames -> new tracker (restart {self.restarts})')
                self.tracker = None
            return
        self.fails = 0
        self.ok += 1
        r, p = wfr.pose.rotation, wfr.pose.translation
        T_wc = to_T(quat_to_mat(r[0], r[1], r[2], r[3]), p)
        prev, self.prev = self.prev, (t_ns, T_wc, T_bc)
        if prev is None:
            return
        H = np.linalg.inv(prev[2]) @ T_bc                        # camera vs base since the last frame = head move
        ang = math.acos(max(-1.0, min(1.0, (np.trace(H[:3, :3]) - 1) / 2)))
        if np.linalg.norm(H[:3, 3]) > HEAD_MOVE[0] or ang > HEAD_MOVE[1]:
            if t_ns > self.head_until:
                self.head_moves += 1
            self.head_until = t_ns + int(HEAD_SETTLE_S * 1e9)
        if t_ns <= self.head_until:                              # head moving: this step is not counted
            return
        dt = (t_ns - prev[0]) / 1e9
        if not 0 < dt < 0.5:
            return
        # motion of base_link since the last frame, in base_link. The base moves on the floor: only x, y, yaw are
        # integrated (a full 3D chain let small arm-TF errors tilt the frame; forward motion then leaked into z).
        D = prev[2] @ np.linalg.inv(prev[1]) @ T_wc @ np.linalg.inv(T_bc)
        dx, dy, dyaw = float(D[0, 3]), float(D[1, 3]), math.atan2(D[1, 0], D[0, 0])
        if math.hypot(dx, dy) / dt > MAX_V or abs(dyaw) / dt > MAX_W:
            self.jumps += 1                                      # not a motion of this robot: dropped
            return
        c, sn = math.cos(self.yaw + dyaw / 2), math.sin(self.yaw + dyaw / 2)
        self.x += c * dx - sn * dy
        self.y += sn * dx + c * dy
        self.yaw = math.atan2(math.sin(self.yaw + dyaw), math.cos(self.yaw + dyaw))
        m = Odometry()
        m.header.stamp = ir.header.stamp
        m.header.frame_id, m.child_frame_id = 'odom_vslam', 'base_link'
        m.pose.pose.position.x, m.pose.pose.position.y = self.x, self.y
        m.pose.pose.orientation.z, m.pose.pose.orientation.w = math.sin(self.yaw / 2), math.cos(self.yaw / 2)
        m.pose.covariance[0] = m.pose.covariance[7] = 0.01
        m.pose.covariance[35] = 0.02
        m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z = dx / dt, dy / dt, dyaw / dt
        m.twist.covariance[0] = m.twist.covariance[7] = 0.002
        m.twist.covariance[35] = 0.005
        self.pub.publish(m)

    def report(self):
        self.get_logger().info(f'tracked {self.ok} frames, head moves {self.head_moves}, jumps dropped {self.jumps}, tracker restarts {self.restarts}')


def main():
    rclpy.init()
    node = VslamOdom()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()


if __name__ == '__main__':
    main()
