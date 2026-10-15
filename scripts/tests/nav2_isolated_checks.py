"""Checks of the INSTALLED Nav2 behaviours and collision monitor on an isolated ROS domain (no base, no wheels):
fake TF, fake scan, fake local costmap. Run through nav2_isolated_checks.sh (it starts the Nav2 nodes with the
robot's own parameter files). Source of what is tested: docs/research/nav2_reference.md sections 20, 24, 25.

Prints one line per case and a JSON summary; exit code 0 = every expectation in EXPECT held."""
import json, math, sys, time

import rclpy
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import Point32, PolygonStamped, TransformStamped, Twist
from nav2_msgs.action import AssistedTeleop, Spin
from nav2_msgs.msg import Costmap
from rclpy.action import ActionClient
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import LaserScan
from tf2_ros import StaticTransformBroadcaster

FOOT = [(0.165, 0.13), (0.165, -0.125), (-0.16, -0.125), (-0.16, 0.13)]     # nav2.yaml footprint + 0.01 padding
RES, N = 0.05, 60                                                          # 3 x 3 m local costmap, robot in the centre
LASER_X = 0.048156


class Rig:
    def __init__(self):
        self.n = rclpy.create_node('nav2_isolated_checks')
        self.tf = StaticTransformBroadcaster(self.n)
        tl = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL)
        self.cost_pub = self.n.create_publisher(Costmap, 'local_costmap/costmap_raw', tl)
        self.foot_pub = self.n.create_publisher(PolygonStamped, 'local_costmap/published_footprint', 10)
        self.scan_pub = self.n.create_publisher(LaserScan, 'scan', 10)
        self.teleop_pub = self.n.create_publisher(Twist, 'cmd_vel_teleop', 10)
        self.smoothed_pub = self.n.create_publisher(Twist, 'cmd_vel_smoothed', 10)
        self.nav_out, self.cm_out = [], []
        self.n.create_subscription(Twist, 'cmd_vel_nav', lambda m: self.nav_out.append((time.time(), m.linear.x, m.linear.y, m.angular.z)), 50)
        self.n.create_subscription(Twist, 'cmd_vel', lambda m: self.cm_out.append((time.time(), m.linear.x, m.linear.y, m.angular.z)), 50)
        self.teleop = ActionClient(self.n, AssistedTeleop, 'assisted_teleop')
        self.spin_ac = ActionClient(self.n, Spin, 'spin')
        self.scan_pts, self.scan_on = [], True
        self.n.create_timer(0.1, self.tick)
        ts = []
        for parent, child, x, yaw in (('odom', 'base_link', 0.0, 0.0), ('base_link', 'laser', LASER_X, math.pi)):
            t = TransformStamped(); t.header.stamp = self.n.get_clock().now().to_msg()
            t.header.frame_id, t.child_frame_id = parent, child
            t.transform.translation.x = x
            t.transform.rotation.z, t.transform.rotation.w = math.sin(yaw / 2), math.cos(yaw / 2)
            ts.append(t)
        self.tf.sendTransform(ts)

    def tick(self):
        p = PolygonStamped(); p.header.frame_id = 'odom'; p.header.stamp = self.n.get_clock().now().to_msg()
        p.polygon.points = [Point32(x=float(x), y=float(y)) for x, y in FOOT]
        self.foot_pub.publish(p)
        if self.scan_on:
            self.scan_pub.publish(self.scan_msg())

    def scan_msg(self):
        """360 rays of 1 deg in the laser frame (mounted yaw pi at x LASER_X); base-frame points -> the nearest ray."""
        s = LaserScan(); s.header.frame_id = 'laser'; s.header.stamp = self.n.get_clock().now().to_msg()
        s.angle_min, s.angle_increment = -math.pi, math.radians(1.0)
        s.angle_max = s.angle_min + 359 * s.angle_increment
        s.range_min, s.range_max = 0.12, 8.0
        r = [float('inf')] * 360
        for bx, by in self.scan_pts:
            lx, ly = -(bx - LASER_X), -by                       # base -> laser (rotation pi)
            i = int(round((math.atan2(ly, lx) - s.angle_min) / s.angle_increment)) % 360
            r[i] = math.hypot(lx, ly)
        s.ranges = r
        return s

    def costmap(self, boxes):
        """Free 3 x 3 m costmap with lethal boxes (x0, x1, y0, y1) in odom."""
        m = Costmap(); m.header.frame_id = 'odom'; m.header.stamp = self.n.get_clock().now().to_msg()
        m.metadata.layer = 'master'; m.metadata.resolution = RES
        m.metadata.size_x = m.metadata.size_y = N
        m.metadata.origin.position.x = m.metadata.origin.position.y = -N * RES / 2
        m.metadata.origin.orientation.w = 1.0
        m.metadata.map_load_time = m.metadata.update_time = m.header.stamp
        d = [0] * (N * N)
        for x0, x1, y0, y1 in boxes:
            for iy in range(N):
                for ix in range(N):
                    x, y = -N * RES / 2 + (ix + 0.5) * RES, -N * RES / 2 + (iy + 0.5) * RES
                    if x0 <= x <= x1 and y0 <= y <= y1:
                        d[iy * N + ix] = 254
        m.data = d
        self.cost_pub.publish(m)

    def spin(self, s):
        end = time.time() + s
        while time.time() < end:
            rclpy.spin_once(self.n, timeout_sec=0.02)

    def wait_goal(self, ac, goal):
        if not ac.wait_for_server(timeout_sec=20):
            return None
        f = ac.send_goal_async(goal)
        rclpy.spin_until_future_complete(self.n, f, timeout_sec=10)
        gh = f.result()
        return gh if gh is not None and gh.accepted else None

    def teleop_case(self, boxes, vx, vy):
        """Mean output twist of AssistedTeleop for a constant input, with the given lethal boxes."""
        self.costmap(boxes); self.spin(1.0)
        g = AssistedTeleop.Goal(); g.time_allowance = Duration(sec=6)
        gh = self.wait_goal(self.teleop, g)
        if gh is None:
            return None
        self.spin(0.5); self.nav_out.clear()
        cmd = Twist(); cmd.linear.x, cmd.linear.y = vx, vy
        end = time.time() + 2.0
        while time.time() < end:
            self.teleop_pub.publish(cmd); self.spin(0.05)
        out = self.nav_out[len(self.nav_out) // 2:]
        gh.cancel_goal_async(); self.teleop_pub.publish(Twist()); self.spin(1.0)
        if not out:
            return None
        return [round(sum(o[i] for o in out) / len(out), 3) for i in (1, 2, 3)]

    def spin_case(self, boxes):
        """What Spin publishes for a 90 deg goal with the given lethal boxes (the fake robot never turns)."""
        self.costmap(boxes); self.spin(1.0); self.nav_out.clear()
        g = Spin.Goal(); g.target_yaw = 1.57; g.time_allowance = Duration(sec=3)
        gh = self.wait_goal(self.spin_ac, g)
        if gh is None:
            return None
        self.spin(2.0)
        out = list(self.nav_out)
        gh.cancel_goal_async(); self.spin(1.5)
        return dict(commands=len(out), first_wz=round(out[0][3], 3) if out else None,
                    max_wz=round(max(abs(o[3]) for o in out), 3) if out else None)

    def cm_case(self, pts, vx, vy, wz, scan_on=True, settle=1.5):
        """Collision monitor output for a constant input with the given scan points (base frame)."""
        self.scan_pts, self.scan_on = pts, scan_on
        self.spin(settle); self.cm_out.clear()
        cmd = Twist(); cmd.linear.x, cmd.linear.y, cmd.angular.z = vx, vy, wz
        end = time.time() + 1.5
        while time.time() < end:
            self.smoothed_pub.publish(cmd); self.spin(0.05)
        out = self.cm_out[len(self.cm_out) // 2:]
        self.scan_on = True
        if not out:
            return None
        return [round(sum(o[i] for o in out) / len(out), 3) for i in (1, 2, 3)]


def main():
    which = set(sys.argv[1:]) or {'teleop', 'spin', 'cm'}
    rclpy.init(); r = Rig(); r.spin(3.0)
    LEFT, RIGHT = (-0.3, 0.3, 0.25, 0.5), (-0.3, 0.3, -0.5, -0.25)
    res = {}
    if 'teleop' in which:
        res['teleop_free_strafe_left'] = r.teleop_case([], 0.0, 0.2)
        res['teleop_wall_left_strafe_left'] = r.teleop_case([LEFT], 0.0, 0.2)       # must be slowed
        res['teleop_wall_right_strafe_left'] = r.teleop_case([RIGHT], 0.0, 0.2)     # must NOT be slowed
        res['teleop_wall_ahead_forward'] = r.teleop_case([(0.3, 0.5, -0.3, 0.3)], 0.2, 0.0)   # must be slowed
    if 'spin' in which:
        res['spin_free'] = r.spin_case([])
        # a post 3 cm in front of the nose: outside the footprint now, swept by the front corners after ~38 deg
        res['spin_post_at_nose'] = r.spin_case([(0.18, 0.22, -0.03, 0.03)])
    if 'cm' in which:
        res['cm_free_forward'] = r.cm_case([], 0.2, 0.0, 0.0)
        res['cm_1pt_in_slow_zone'] = r.cm_case([(0.35, 0.0)], 0.2, 0.0, 0.0)
        res['cm_2pt_in_slow_zone'] = r.cm_case([(0.35, 0.0), (0.35, 0.01)], 0.2, 0.0, 0.0)
        # approach look-ahead: obstacle 0.45 m ahead at 0.35 m/s = 0.76 s to the polygon edge (limit 1.2 s)
        res['cm_1pt_ahead_0.45'] = r.cm_case([(0.45, 0.0)], 0.35, 0.0, 0.0)
        res['cm_2pt_ahead_0.45'] = r.cm_case([(0.45, 0.0), (0.45, 0.012)], 0.35, 0.0, 0.0)
        res['cm_3pt_ahead_0.45'] = r.cm_case([(0.45, -0.012), (0.45, 0.0), (0.45, 0.012)], 0.35, 0.0, 0.0)
        res['cm_1pt_inside_footprint'] = r.cm_case([(0.17, 0.05)], 0.2, 0.0, 0.0)
        res['cm_2pt_inside_footprint'] = r.cm_case([(0.17, 0.05), (0.17, 0.055)], 0.2, 0.0, 0.0)
        res['cm_2pt_inside_footprint_turn_away'] = r.cm_case([(0.17, 0.05), (0.17, 0.055)], 0.0, 0.0, 0.8)
        res['cm_3pt_inside_footprint'] = r.cm_case([(0.17, 0.045), (0.17, 0.05), (0.17, 0.055)], 0.2, 0.0, 0.0)
        res['cm_2pt_left_strafe_left'] = r.cm_case([(0.0, 0.30), (0.01, 0.30)], 0.0, 0.2, 0.0)
        res['cm_2pt_left_strafe_right'] = r.cm_case([(0.0, 0.30), (0.01, 0.30)], 0.0, -0.2, 0.0)
        # the LiDAR goes silent while a wall is 2 cm in front of the nose and the command says forward
        res['cm_scan_silent_wall_at_nose'] = r.cm_case([(0.19, 0.0), (0.19, 0.01)], 0.2, 0.0, 0.0, scan_on=False, settle=2.5)
    if 'cmdebug' in which:
        for x, y in ((0.17, 0.05), (0.14, 0.10), (0.12, 0.12), (-0.10, 0.12), (0.0, 0.14), (0.16, 0.0)):
            res[f'd_1pt_{x}_{y}_fwd'] = r.cm_case([(x, y)], 0.2, 0.0, 0.0, settle=3.0)
        for x in (0.14, 0.16, 0.18, 0.20):
            res[f'd_2pt_x{x}_turn'] = r.cm_case([(x, 0.05), (x, 0.058)], 0.0, 0.0, 0.8, settle=3.0)
    if 'params' in which or which >= {'teleop', 'spin', 'cm'}:      # what the installed behavior_server really loaded
        from rcl_interfaces.srv import GetParameters
        cli = r.n.create_client(GetParameters, '/behavior_server/get_parameters')
        names = ['backup.acceleration_limit', 'backup.deceleration_limit', 'backup.minimum_speed',
                 'drive_on_heading.acceleration_limit', 'drive_on_heading.deceleration_limit',
                 'drive_on_heading.minimum_speed', 'behavior_plugins']
        if cli.wait_for_service(timeout_sec=10):
            f = cli.call_async(GetParameters.Request(names=names))
            rclpy.spin_until_future_complete(r.n, f, timeout_sec=10)
            if f.result() is not None:
                for nm, v in zip(names, f.result().values):
                    res['param ' + nm] = v.double_value if v.type == 3 else list(v.string_array_value)
    for k, v in res.items():
        print(f'{k:36s} {v}', flush=True)
    print('JSON ' + json.dumps(res), flush=True)
    r.n.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
