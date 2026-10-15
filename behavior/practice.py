"""The robot practises with its own body and records what happens (owner 2026-10-02: "stop trying to control the
robot and start trying to train it"; docs/research/training_the_body.md, way D: self-modeling by babbling).

It makes small moves of its own choosing - any direction, a range of speeds, sometimes turning - each about 25 cm and
then back the way it came, only into floor its own costmap shows free. Nav2's built-in AssistedTeleop takes the
commands, so every move is collision-checked against the LiDAR + nvblox costmap, then passes the velocity smoother and
the collision monitor like any other motion. Before each move it notes what the floor ahead looks like; after it, what
actually happened (camera odometry, LiDAR odometry, map pose, gyro). Nothing here decides how the robot should drive:
it only produces experience. behavior/learn_body.py turns that into the robot's own body model.

Log: ~/practice/<run>/trials.jsonl (one line per move) + floor_<n>.jpg. Run: scripts/explore_run.sh with PRACTICE=1
(rosorin-practice.service). Ends on: time limit, e-stop that is not its own reflex, plugged in, low battery."""
import json, math, os, random, sys, time

import cv2
import numpy as np
import rclpy
from ament_index_python.packages import get_package_prefix
from builtin_interfaces.msg import Duration
from geometry_msgs.msg import Twist
from nav2_msgs.action import AssistedTeleop
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import BatteryState, Image, Imu
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from tf2_ros import Buffer, TransformListener

sys.path.insert(0, os.path.expanduser('~/vision'))
from mind_pause import mind_paused  # noqa: E402

SECONDS = float(os.environ.get('PRACTICE_SECONDS', '240'))
REACH = 0.25              # m a move tries to cover (short: it practises, it does not travel)
MAX_MOVE_S = 2.5
SPEEDS = (0.08, 0.12, 0.18, 0.25, 0.35)
# Sideways moves and the stock guard: the apt nav2_behaviors 1.1.20 projects linear.y with the wrong sign in
# AssistedTeleop (upstream #6534, fixed on main only) - a strafe is checked against the side the robot moves AWAY
# from: no slowing for a wall on the strafe side, false slowing for one on the other side (measured on this robot
# 2026-10-02, scripts/tests/nav2_isolated_checks.sh). Sideways practice is NOT switched off for that (owner: sideways
# is not to be disabled): free_ahead() below checks the costmap on the correct side before every move and the
# collision monitor's approach model has the correct sign. But a trial next to a wall on the far side may have been
# slowed for no reason, so every trial records whether the guard was the fixed one (strafe_guard_ok): True only when
# nav2_behaviors comes from the workspace overlay built by scripts/install_nav2_behaviors_fix.sh.
STRAFE_GUARD_OK = not get_package_prefix('nav2_behaviors').startswith('/opt/ros/')
DIRECTIONS = (0, 45, -45, 90, -90)      # deg in the robot's own frame: 0 = ahead, 90 = left. Never backwards into
                                        # floor it has not just covered: the rear is blind (it backed into a screen
                                        # door on 2026-10-02). Backwards only happens as the return leg of a move.
TURNS = (0.0, 0.0, 0.0, 0.5, -0.5)
CLEAR = 0.55              # m of free costmap needed in the direction of a move (reach + body + margin)
FREE_COST = 50            # costmap cost (0-100) that counts as free for practising
LOW_BATTERY_V = 10.0
REFLEX = 'estop topic'


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class Practice:
    def __init__(self):
        self.n = rclpy.create_node('rosorin_practice')
        self.dir = os.path.expanduser(time.strftime('~/practice/%Y%m%d_%H%M%S')); os.makedirs(self.dir)
        self.f = open(os.path.join(self.dir, 'trials.jsonl'), 'a')
        self.st, self.bat, self.cost, self.rgb = {}, [], None, None
        self.vs, self.rf, self.gz = None, None, []
        self.buf = Buffer(); TransformListener(self.buf, self.n)
        self.n.create_subscription(String, '/rosorin_board_driver/state', lambda m: setattr(self, 'st', json.loads(m.data)), 10)
        self.n.create_subscription(BatteryState, '/battery', lambda m: self.bat.append((time.time(), m.voltage)), 10)
        self.n.create_subscription(OccupancyGrid, '/local_costmap/costmap', lambda m: setattr(self, 'cost', m), 1)
        self.n.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: setattr(self, 'rgb', m), qos_profile_sensor_data)
        self.n.create_subscription(Odometry, '/odom_vslam', lambda m: setattr(self, 'vs', self.xyt(m)), 20)
        self.n.create_subscription(Odometry, '/odom_rf2o', lambda m: setattr(self, 'rf', self.xyt(m)), 20)
        self.n.create_subscription(Imu, '/imu/data_raw', lambda m: self.gz.append((time.time(), m.angular_velocity.z)), 200)
        self.pub = self.n.create_publisher(Twist, 'cmd_vel_teleop', 10)
        self.en = self.n.create_client(SetBool, '/rosorin_board_driver/enable')
        self.rs = self.n.create_client(Trigger, '/rosorin_board_driver/reset_estop')
        self.teleop = ActionClient(self.n, AssistedTeleop, 'assisted_teleop')

    @staticmethod
    def xyt(m):
        p = m.pose.pose
        return time.time(), p.position.x, p.position.y, yaw_of(p.orientation)

    def spin(self, s):
        end = time.time() + s
        while time.time() < end:
            rclpy.spin_once(self.n, timeout_sec=0.02)

    def call(self, cli, req, t=20):
        if not cli.wait_for_service(timeout_sec=5):
            return None
        f = cli.call_async(req); end = time.time() + t
        while time.time() < end and not f.done():
            rclpy.spin_once(self.n, timeout_sec=0.02)
        return f.result() if f.done() else None

    def battery(self):
        v = [x for t, x in self.bat if time.time() - t < 20]
        return sum(v) / len(v) if v else None

    def map_pose(self):
        try:
            t = self.buf.lookup_transform('map', 'base_link', Time()).transform
            return t.translation.x, t.translation.y, yaw_of(t.rotation)
        except Exception:
            return None

    def odom_pose(self):
        try:
            t = self.buf.lookup_transform('odom', 'base_link', Time()).transform
            return t.translation.x, t.translation.y, yaw_of(t.rotation)
        except Exception:
            return None

    def free_ahead(self, deg):
        """Is the costmap free for CLEAR m in this body direction, over the width of the robot?"""
        c, p = self.cost, self.odom_pose()
        if c is None or p is None:
            return False
        g = np.array(c.data, np.int16).reshape(c.info.height, c.info.width); r = c.info.resolution
        a = p[2] + math.radians(deg)
        for d in np.arange(0.0, CLEAR + 1e-6, r):
            for off in (-0.14, 0.0, 0.14):
                x = p[0] + d * math.cos(a) - off * math.sin(a); y = p[1] + d * math.sin(a) + off * math.cos(a)
                i, j = int((x - c.info.origin.position.x) / r), int((y - c.info.origin.position.y) / r)
                if not (0 <= i < g.shape[1] and 0 <= j < g.shape[0]) or g[j, i] >= FREE_COST or g[j, i] < 0:
                    return False
        return True

    def floor_look(self, k):
        """What the floor ahead looks like right now: saved crop + its mean colour (Lab) - the robot's own label."""
        m = self.rgb
        if m is None:
            return None
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)[:, :, :3]
        crop = img[int(m.height * 0.55):, int(m.width * 0.25):int(m.width * 0.75)]
        cv2.imwrite(os.path.join(self.dir, f'floor_{k:04d}.jpg'), crop)
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3)
        return [round(float(v), 1) for v in lab.mean(0)] + [round(float(v), 1) for v in lab.std(0)]

    def start_teleop(self):
        if not self.teleop.wait_for_server(timeout_sec=10):
            return None
        g = AssistedTeleop.Goal(); g.time_allowance = Duration(sec=int(SECONDS) + 60)
        f = self.teleop.send_goal_async(g)
        rclpy.spin_until_future_complete(self.n, f, timeout_sec=10)
        gh = f.result()
        return gh if gh is not None and gh.accepted else None

    def move(self, k, deg, speed, wz, kind):
        """One move: command it for the time REACH would take, then measure what happened."""
        secs = min(MAX_MOVE_S, REACH / speed) if speed > 0 else 2.0
        vx, vy = speed * math.cos(math.radians(deg)), speed * math.sin(math.radians(deg))
        look = self.floor_look(k)
        self.spin(0.3)
        v0, r0, m0, t0 = self.vs, self.rf, self.map_pose(), time.time()
        cmd = Twist(); cmd.linear.x, cmd.linear.y, cmd.angular.z = vx, vy, wz
        while time.time() - t0 < secs and not self.st.get('estop'):
            self.pub.publish(cmd); self.spin(0.1)
        stopped = self.st.get('estop')
        self.pub.publish(Twist()); self.spin(0.9)
        v1, r1, m1, t1 = self.vs, self.rf, self.map_pose(), time.time()
        gyro = sum(b[1] * (b[0] - a[0]) for a, b in zip(self.gz, self.gz[1:]) if t0 <= b[0] <= t1)
        self.gz = self.gz[-50:]

        def moved(a, b):
            """(distance, part of it along the commanded direction, heading change) in the robot's start frame."""
            if a is None or b is None:
                return None
            x0, y0, th0 = a[-3:]; x1, y1, th1 = b[-3:]
            dx, dy = x1 - x0, y1 - y0
            bx, by = math.cos(th0) * dx + math.sin(th0) * dy, -math.sin(th0) * dx + math.cos(th0) * dy
            along = bx * math.cos(math.radians(deg)) + by * math.sin(math.radians(deg))
            return [round(math.hypot(dx, dy), 3), round(along, 3), round(math.atan2(math.sin(th1 - th0), math.cos(th1 - th0)), 3)]
        rec = dict(k=k, t=round(t0, 2), kind=kind, dir_deg=deg, speed=speed, wz=wz, secs=round(secs, 2),
                   asked_m=round(speed * secs, 3), camera=moved(v0, v1), lidar=moved(r0, r1), map=moved(m0, m1),
                   gyro_turn=round(gyro, 3), stopped_by=stopped, floor=look, battery=round(self.battery() or 0, 2),
                   strafe_guard_ok=STRAFE_GUARD_OK)
        self.f.write(json.dumps(rec) + '\n'); self.f.flush()
        print(json.dumps(rec), flush=True)
        return rec

    def recover(self):
        if self.st.get('estop') != REFLEX:
            return False
        self.call(self.rs, Trigger.Request()); self.spin(0.8)
        r = self.call(self.en, SetBool.Request(data=True))
        return bool(r and r.success)

    def run(self):
        end = time.time() + 20
        while time.time() < end and not (self.st and self.bat and self.cost is not None):
            rclpy.spin_once(self.n, timeout_sec=0.1)
        if not self.st or self.st.get('plugged') or self.cost is None:
            print(json.dumps(dict(ev='abort', state=bool(self.st), plugged=self.st.get('plugged'), costmap=self.cost is not None))); return
        if self.st.get('estop') == REFLEX:
            self.call(self.rs, Trigger.Request()); self.spin(0.5)
        r = self.call(self.en, SetBool.Request(data=True))
        if not (r and r.success):
            print(json.dumps(dict(ev='abort', reason='wheel enable refused'))); return
        self.spin(4.0)
        gh = self.start_teleop()
        if gh is None:
            print(json.dumps(dict(ev='abort', reason='assisted teleop not available'))); return
        print(json.dumps(dict(ev='start', directions=DIRECTIONS, strafe_guard_ok=STRAFE_GUARD_OK)), flush=True)
        t_end, k, skipped = time.time() + SECONDS, 0, 0
        while time.time() < t_end:
            why = self.st.get('estop') or (self.st.get('plugged') and 'plugged in') or \
                ((self.battery() or 99) < LOW_BATTERY_V and 'low battery')
            if why and not (why == REFLEX and self.recover()):
                print(json.dumps(dict(ev='stop', reason=why))); break
            if random.random() < 0.15:                        # a turn on the spot
                self.move(k, 0, 0.0, random.choice((0.5, -0.5, 1.0, -1.0)), 'turn'); k += 1
                continue
            deg = random.choice(DIRECTIONS)
            if not self.free_ahead(deg):
                skipped += 1; self.spin(0.05)
                if skipped % 40 == 0:                         # nothing free around it: turn and look again
                    self.move(k, 0, 0.0, random.choice((0.5, -0.5)), 'turn'); k += 1
                continue
            speed, wz = random.choice(SPEEDS), random.choice(TURNS)
            out = self.move(k, deg, speed, wz, 'out'); k += 1
            if out['stopped_by'] and not self.recover():
                continue
            back = deg - 180 if deg > 0 else deg + 180        # then back the way it came (floor it has just covered)
            self.move(k, back, speed, -wz, 'back'); k += 1
        try:
            gh.cancel_goal_async()
        except Exception:
            pass
        self.pub.publish(Twist()); self.spin(0.5)
        print(json.dumps(dict(ev='done', trials=k, skipped_not_free=skipped, dir=self.dir)), flush=True)


def main():
    rclpy.init(); p = Practice()
    try:
        with mind_paused(reason='body practice'):
            p.run()
    finally:
        try: p.call(p.en, SetBool.Request(data=False))
        except Exception as ex: print('disable failed:', ex, flush=True)
        sys.stdout.flush()
        os._exit(0)


if __name__ == '__main__':
    main()
