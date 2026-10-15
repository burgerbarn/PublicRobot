"""rf2o laser odometry -> robot body motion on /odom_lidar (the EKF's motion input; the board has no wheel encoders).
rf2o runs with base_frame_id = laser (config/rf2o.yaml): it reports the LASER's own motion and never looks up the
mounting. (2026-09-30: with base_frame_id base_link, rf2o looked up base_link->laser once on its first scan; at boot
that raced the static TF - "laser does not exist" - and the sign of its output flipped between restarts.)
Here: laser displacement over WIN s -> laser-frame velocity -> base_link velocity with the fixed, verified mount
(yaw pi, x 0.048 m: LiDAR ranges matched the depth camera, 0.15 m median, 2026-09-30):
    v_base = R(pi) v_laser - w x r_laser  (w = yaw rate, r_laser = (0.048, 0) in base_link)
Scale: rf2o read ~66-76 % of the true distance in self-checks (open). EKF uses vx, vy only (yaw rate from the gyro)."""
import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

WIN = 0.3
MOUNT_YAW, MOUNT_X = math.pi, 0.048
SIGN = -1.0     # set by the robot's floor self-check 2026-09-30: +1 gave -0.374 m for a 0.371 m forward move (magnitude exact)


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class Rf2oFix(Node):
    def __init__(self):
        super().__init__('rf2o_fix')
        self.pub = self.create_publisher(Odometry, 'odom_lidar', 20)
        self.create_subscription(Odometry, 'odom_rf2o', self.on_odom, 20)
        self.hist = []
        self.x = self.y = self.th = 0.0                           # integrated base pose (for logs / self-checks)
        self.last_t = None

    def on_odom(self, m):
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        p = m.pose.pose.position
        yl = yaw_of(m.pose.pose.orientation)
        self.hist.append((t, p.x, p.y, yl))
        self.hist = [h for h in self.hist if t - h[0] <= WIN + 0.15]
        old = [h for h in self.hist if h[0] <= t - WIN + 0.02]
        vx = vy = wz = 0.0
        if old:
            t0, x0, y0, yl0 = old[-1]
            dt = max(t - t0, 1e-3)
            dx, dy = (p.x - x0) / dt, (p.y - y0) / dt
            vlx = math.cos(yl0) * dx + math.sin(yl0) * dy            # laser-frame velocity
            vly = -math.sin(yl0) * dx + math.cos(yl0) * dy
            wz = math.atan2(math.sin(yl - yl0), math.cos(yl - yl0)) / dt
            c, s = math.cos(MOUNT_YAW), math.sin(MOUNT_YAW)
            vx = SIGN * (c * vlx - s * vly)
            vy = SIGN * (s * vlx + c * vly) - wz * MOUNT_X            # lever arm: the laser sits 4.8 cm ahead of base_link
        if self.last_t is not None:
            dtp = max(t - self.last_t, 0.0)
            self.x += (vx * math.cos(self.th) - vy * math.sin(self.th)) * dtp
            self.y += (vx * math.sin(self.th) + vy * math.cos(self.th)) * dtp
            self.th += wz * dtp
        self.last_t = t
        o = Odometry()
        o.header.stamp = m.header.stamp
        o.header.frame_id, o.child_frame_id = 'odom_lidar', 'base_link'
        o.pose.pose.position.x, o.pose.pose.position.y = self.x, self.y
        o.pose.pose.orientation.z, o.pose.pose.orientation.w = math.sin(self.th / 2), math.cos(self.th / 2)
        o.twist.twist.linear.x, o.twist.twist.linear.y, o.twist.twist.angular.z = vx, vy, wz
        cov = [0.0] * 36
        cov[0] = cov[7] = 0.0025; cov[35] = 0.01                    # twist: vx, vy (0.05 m/s)^2, wz
        o.twist.covariance = cov
        self.pub.publish(o)


def main():
    rclpy.init()
    n = Rf2oFix()
    try:
        rclpy.spin(n)
    except KeyboardInterrupt:
        pass
    finally:
        n.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
