"""One self-run exploration from standard parts (PLAN.md M1). The robot maps as it goes (slam_toolbox), Nav2 drives,
LiDAR + nvblox (depth camera, GPU) mark obstacles, explore_lite picks frontiers. This script only starts and stops
them - no navigation logic here:
  mind paused -> wheels on (arm to the drive pose, camera ahead) -> explore_lite until no frontier is left (a closed
  room has none after the first look: 13 s on 2026-10-02) -> then floor it has not driven over yet, farthest first
  (plain Nav2 goals on the global costmap) -> until TIME_LIMIT or all reachable floor visited -> back to where it
  started -> wheels off.
At each new vantage point it stops and looks around with the arm (the mind's own room scan: what and who is here,
the world model) - owner 2026-10-06: "stop and use the arm to look around to gather more input". It does not quit
easily (owner 2026-10-06: "he quits too easy"): a goal that fails from inside an obstacle's inflation ring gets an
escape crawl (unstick.py) and another go; five failures in a row earn fresh costmaps and a second wind; home gets up
to HOME_TRIES tries with escapes between. Ends early on: e-stop (gamepad, contact reflex), plugged in, low battery.
Log: ~/explore/<run>/log.jsonl (state every 5 s) + summary.json. Runs as rosorin-explore.service (explore_run.sh)."""
import glob, json, math, os, subprocess, sys, time

import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from nav2_msgs.srv import ClearEntireCostmap
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.action import ActionClient
from rclpy.time import Time
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

REFLEX = 'estop topic'    # the robot's own stop (contact reflex); gamepad e-stops carry another reason
MAX_STALLS_HERE = 3       # reflex stops within 0.5 m of each other before the run ends
from tf2_ros import Buffer, TransformListener

sys.path.insert(0, os.path.expanduser('~/vision'))
from mind_pause import mind_paused  # noqa: E402
from unstick import Unstick  # noqa: E402

TIME_LIMIT = float(os.environ.get('EXPLORE_SECONDS', '600'))
LOW_BATTERY_V = 10.0      # owner 2026-10-01; compared against a 20 s average
HOME_TIMEOUT = 120.0      # per try
HOME_TRIES, HOME_BUDGET = 4, 360.0   # tries to get home, and the most time spent on them (2026-10-06: one try quit)
SECOND_WINDS = 2          # after five failed goals in a row: fresh costmaps and on again, this many times
LOOK_EVERY, LOOK_TIMEOUT = 60.0, 45.0   # s between look-around stops; longest wait for the mind's room scan (25 s + looks)
GOAL_TIMEOUT = 60.0
VISIT_R = 0.5              # m: floor this close to where the robot has been counts as visited
GOAL_COST = 60            # goals only on cells cheaper than this (0-100; 99 = touching an obstacle's inflation core)
COVERED = 0.90            # share of reachable floor visited = done
EXPLORE_PARAMS = '/home/burgerbarn/ros2_ws/install/rosorin_base/share/rosorin_base/config/explore.yaml'


class Run:
    def __init__(self):
        self.n = rclpy.create_node('rosorin_explore')
        self.dir = os.path.expanduser(time.strftime('~/explore/%Y%m%d_%H%M%S')); os.makedirs(self.dir)
        self.f = open(os.path.join(self.dir, 'log.jsonl'), 'a')
        self.st, self.bat, self.vs, self.dist, self.estops = {}, [], None, 0.0, []
        self.cost, self.track, self.failed, self.goals, self.stalls = None, [], [], {}, []
        self.buf = Buffer(); TransformListener(self.buf, self.n)
        self.n.create_subscription(String, '/rosorin_board_driver/state', self.on_state, 10)
        self.n.create_subscription(BatteryState, '/battery', lambda m: self.bat.append((time.time(), m.voltage)), 10)
        self.n.create_subscription(Odometry, '/odom_vslam', self.on_vs, 20)
        self.n.create_subscription(OccupancyGrid, '/global_costmap/costmap', lambda m: setattr(self, 'cost', m),
                                   QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.en = self.n.create_client(SetBool, '/rosorin_board_driver/enable')
        self.resume = self.n.create_publisher(Bool, 'explore/resume', 1)
        self.nav = ActionClient(self.n, NavigateToPose, 'navigate_to_pose')
        self.un = Unstick(self.n, self.buf)
        self.clear_srv = [self.n.create_client(ClearEntireCostmap, f'/{c}_costmap/clear_entirely_{c}_costmap')
                          for c in ('global', 'local')]
        self.mind_en = self.n.create_client(SetBool, '/rosorin_mind/enable')
        self.heard = self.n.create_publisher(String, '/buddy/heard', 10)
        self.mind = {}
        self.n.create_subscription(String, '/rosorin_mind/mode', lambda m: self.mind.update(json.loads(m.data)),
                                   QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.last_look, self.looks, self.escapes = 0.0, 0, 0

    def log(self, ev, **kw):
        d = dict(kw, t=round(time.time(), 2), ev=ev)
        self.f.write(json.dumps(d) + '\n'); self.f.flush(); print(json.dumps(d), flush=True)

    def on_state(self, m):
        st = json.loads(m.data)
        if st.get('estop') and not self.st.get('estop'):
            self.estops.append(st['estop'])
        self.st = st

    def on_vs(self, m):                                       # distance actually driven (camera odometry)
        p = (m.pose.pose.position.x, m.pose.pose.position.y)
        if self.vs is not None:
            self.dist += math.dist(p, self.vs)
        self.vs = p

    def spin(self, s):
        end = time.time() + s
        while time.time() < end:
            rclpy.spin_once(self.n, timeout_sec=0.05)

    def wheels(self, on):
        if not self.en.wait_for_service(timeout_sec=5):
            return False
        f = self.en.call_async(SetBool.Request(data=on))
        end = time.time() + 20
        while time.time() < end and not f.done():
            rclpy.spin_once(self.n, timeout_sec=0.05)
        ok = bool(f.done() and f.result() and f.result().success)
        self.log('wheels', on=on, ok=ok)
        return ok

    def call(self, cli, req, t=20):
        if not cli.wait_for_service(timeout_sec=5):
            return None
        f = cli.call_async(req); end = time.time() + t
        while time.time() < end and not f.done():
            rclpy.spin_once(self.n, timeout_sec=0.05)
        return f.result() if f.done() else None

    def pose(self):
        try:
            t = self.buf.lookup_transform('map', 'base_link', Time()).transform
            q = t.rotation
            return round(t.translation.x, 2), round(t.translation.y, 2), \
                round(math.degrees(math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))))
        except Exception:
            return None

    def battery(self):
        v = [x for t, x in self.bat if time.time() - t < 20]
        return sum(v) / len(v) if v else None

    def recover(self):
        """After the robot's own reflex stop (it stalled and stopped - nothing was pushed): note where, clear the
        stop, wheels back on. True if it may carry on (another goal); False once it has stalled MAX_STALLS_HERE times
        around the same spot, or the stop is not its own (gamepad = the owner's)."""
        if self.st.get('estop') != REFLEX:
            return False
        p = self.pose()
        self.stalls.append(p[:2] if p else (9e9, 9e9))
        here = sum(1 for q in self.stalls if p and math.dist(q, p[:2]) < 0.5)
        self.log('stall', pose=p, stalls_here=here, total=len(self.stalls))
        if here >= MAX_STALLS_HERE:
            return False
        c = self.n.create_client(Trigger, '/rosorin_board_driver/reset_estop')
        if not c.wait_for_service(timeout_sec=5):
            return False
        f = c.call_async(Trigger.Request()); end = time.time() + 5
        while time.time() < end and not f.done():
            rclpy.spin_once(self.n, timeout_sec=0.05)
        self.spin(1.0)
        return self.wheels(True) and not self.st.get('estop')

    def boxed(self):
        p = self.pose()
        return bool(p and self.un.boxed_in(self.cost, p[0], p[1]))

    def escape(self):
        """Out of a spot Nav2 cannot plan from (unstick.py). (cleared, detail)."""
        self.escapes += 1
        return self.un.escape(lambda: self.cost, self.pose, self.stop_reason, self.spin, self.log)

    def clear_costmaps(self):
        for c in self.clear_srv:
            self.call(c, ClearEntireCostmap.Request(), 5)
        self.log('costmaps_cleared')

    def look_around(self):
        """Stop and look around with the arm at a new vantage point (owner 2026-10-06): wheels off, the mind gets its
        arm back and runs its own room scan (what and who is here, what a look there is worth, the world model), then
        the mind is paused again and the arm returns to the drive pose as the wheels come back. The time spent is
        not taken from the drive."""
        now = time.time()
        if now - self.last_look < LOOK_EVERY or self.stop_reason() or now > self.deadline:
            return
        self.last_look = now
        if not self.wheels(False):
            return
        scans = lambda: set(glob.glob(os.path.expanduser('~/vision/roomscans/*/meta.json')))
        before = scans()
        self.call(self.mind_en, SetBool.Request(data=True), 5); self.spin(1.0)
        self.heard.publish(String(data=json.dumps({'event': 'scan_room', 't': time.time()})))
        started = False
        while time.time() - now < LOOK_TIMEOUT and not self.stop_reason():
            self.spin(0.5); self.note()
            started = started or self.mind.get('why') == 'looking around the room'
            if scans() - before:
                break
            if not started and time.time() - now > 10:         # the mind did not take it up (cooling, no arm): drive on
                break
        new = scans() - before
        self.call(self.mind_en, SetBool.Request(data=False), 5)
        t = time.time() + 8
        while time.time() < t and (self.mind.get('mode') != 'paused' or self.mind.get('have_arm')):
            self.spin(0.1)
        ok = self.wheels(True)
        if not ok:
            self.spin(2.0); ok = self.wheels(True)
        self.deadline += time.time() - now
        self.looks += 1
        self.log('look_around', scans=len(new), started=started, secs=round(time.time() - now, 1), wheels=ok,
                 pose=self.pose())

    def go_home(self, home):
        """Home with persistence: up to HOME_TRIES goals within HOME_BUDGET s; between tries, out of a boxed-in spot
        (escape) or fresh costmaps. Only after that does it say it could not get home."""
        end = time.time() + HOME_BUDGET; back = 'skipped'
        for i in range(HOME_TRIES):
            if self.stop_reason() or time.time() > end:
                break
            self.deadline = min(end, time.time() + HOME_TIMEOUT)
            back = self.go(home[0], home[1], math.radians(home[2]), HOME_TIMEOUT)
            self.log('home_try', n=i + 1, result=back, pose=self.pose())
            if back == 'SUCCEEDED':
                break
            if back == 'STOPPED' and not self.recover():
                break
            if self.boxed():
                self.escape()
            else:
                self.clear_costmaps()
            self.spin(3.0)
        return back

    def stop_reason(self):
        if self.st.get('estop'): return f"e-stop: {self.st['estop']}"
        if self.st.get('plugged'): return 'plugged in'
        b = self.battery()
        if b is not None and b < LOW_BATTERY_V: return f'low battery {b:.2f} V'
        return None

    def go(self, x, y, yaw, timeout):
        """One Nav2 goal in the map frame; returns SUCCEEDED / ABORTED / CANCELED / TIMEOUT / STOPPED / ..."""
        if not self.nav.wait_for_server(timeout_sec=10):
            return 'NO_SERVER'
        g = NavigateToPose.Goal(); g.pose = PoseStamped(); g.pose.header.frame_id = 'map'
        g.pose.header.stamp = self.n.get_clock().now().to_msg()
        g.pose.pose.position.x, g.pose.pose.position.y = float(x), float(y)
        g.pose.pose.orientation.z, g.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        f = self.nav.send_goal_async(g)
        rclpy.spin_until_future_complete(self.n, f, timeout_sec=10)
        gh = f.result()
        if gh is None or not gh.accepted:
            return 'REJECTED'
        rf = gh.get_result_async(); end = time.time() + timeout
        while time.time() < end and not rf.done() and not self.stop_reason() and time.time() < self.deadline:
            rclpy.spin_once(self.n, timeout_sec=0.1); self.note()
        if not rf.done():
            gh.cancel_goal_async(); self.spin(1.0)
            return 'STOPPED' if self.stop_reason() else 'TIMEOUT'
        return {4: 'SUCCEEDED', 5: 'CANCELED', 6: 'ABORTED'}.get(rf.result().status, str(rf.result().status))

    def note(self):
        """Remember where the robot has been (map frame) and log its state every 5 s."""
        p = self.pose()
        if p and (not self.track or math.dist(p[:2], self.track[-1]) > 0.05):
            self.track.append(p[:2])
        if time.time() >= self.next_log:
            self.next_log = time.time() + 5
            self.log('state', secs=round(time.time() - self.t0), pose=p, driven_m=round(self.dist, 2),
                     battery=round(self.battery() or 0, 2))

    def pick(self):
        """Floor not yet driven over: the reachable-looking cell farthest from everywhere the robot has been.
        Returns ((x, y) or None, share of floor visited)."""
        c = self.cost
        if c is None or not self.track:
            return None, 0.0
        info, r = c.info, c.info.resolution
        C = np.array(c.data, np.int16).reshape(info.height, info.width)[::2, ::2]      # 10 cm grid is plenty
        jj, ii = np.nonzero((C >= 0) & (C < 99))
        if not len(ii):
            return None, 0.0
        X = info.origin.position.x + (ii * 2 + 1) * r; Y = info.origin.position.y + (jj * 2 + 1) * r
        T = np.array(self.track[::3] + [self.track[-1]])
        d = np.min(np.hypot(X[:, None] - T[None, :, 0], Y[:, None] - T[None, :, 1]), axis=1)
        covered = float((d <= VISIT_R).mean())
        ok = (C[jj, ii] < GOAL_COST) & (d > VISIT_R)
        for fx, fy in self.failed:
            ok &= np.hypot(X - fx, Y - fy) > 0.4
        if not ok.any():
            return None, covered
        k = int(np.argmax(np.where(ok, d, -1.0)))
        return (float(X[k]), float(Y[k])), covered

    def run(self):
        end = time.time() + 20
        while time.time() < end and not (self.st and self.bat):
            rclpy.spin_once(self.n, timeout_sec=0.1)
        if self.st.get('estop') == 'estop topic':             # its own reflex stopped the last run; a new run = go.
            c = self.n.create_client(Trigger, '/rosorin_board_driver/reset_estop')   # gamepad e-stops stay the owner's
            if c.wait_for_service(timeout_sec=5):
                f = c.call_async(Trigger.Request()); end = time.time() + 5
                while time.time() < end and not f.done():
                    rclpy.spin_once(self.n, timeout_sec=0.05)
                self.spin(0.5); self.log('estop_cleared', was='estop topic')
        why = 'no driver state' if not self.st else self.stop_reason()
        if why:
            self.log('abort', reason=why); return
        if not self.wheels(True):
            self.log('abort', reason='wheel enable refused'); return
        self.spin(4.0)                                        # arm settled in the drive pose; map + costmaps filling
        home = self.pose()
        self.t0 = time.time(); self.deadline = self.t0 + TIME_LIMIT; self.next_log = self.t0 + 5
        self.log('start', pose=home, battery=self.battery(), limit_s=TIME_LIMIT)
        ex = subprocess.Popen(['ros2', 'run', 'explore_lite', 'explore', '--ros-args', '--params-file', EXPLORE_PARAMS],
                              stdout=open(os.path.join(self.dir, 'explore_lite.log'), 'w'), stderr=subprocess.STDOUT)
        reason = None
        while reason is None:                                 # phase 1: frontiers (explore_lite)
            self.spin(0.2); self.note()
            reason = self.stop_reason() or ('time limit' if time.time() > self.deadline else None)
            if reason is None and (ex.poll() is not None or (time.time() - self.t0 > 15 and 'Exploration stopped' in
                                   open(os.path.join(self.dir, 'explore_lite.log')).read())):
                break
        self.resume.publish(Bool(data=False)); self.spin(0.5)
        ex.terminate(); self.spin(1.5)                        # its own "return to start" goal is replaced by ours
        self.log('frontiers_done', secs=round(time.time() - self.t0), driven_m=round(self.dist, 2), stopped=reason)
        fails, winds = 0, SECOND_WINDS
        if reason and self.recover():                         # stalled during the frontier phase: carry on below
            reason = None
        if reason is None:
            self.look_around()                                # the first vantage point: what is here
        while reason is None:                                 # phase 2: floor not driven over yet
            reason = self.stop_reason() or ('time limit' if time.time() > self.deadline else None)
            if reason and self.recover():
                reason = None
            if reason: break
            goal, covered = self.pick()
            if goal is None or covered >= COVERED:
                reason = f'floor covered {covered:.0%}' if covered >= COVERED else 'no reachable floor left'; break
            p = self.pose()
            res = self.go(goal[0], goal[1], math.atan2(goal[1] - p[1], goal[0] - p[0]) if p else 0.0, GOAL_TIMEOUT)
            self.goals[res] = self.goals.get(res, 0) + 1
            self.log('goal', goal=[round(goal[0], 2), round(goal[1], 2)], result=res, covered=round(covered, 2),
                     pose=self.pose(), driven_m=round(self.dist, 2))
            if res == 'STOPPED' and self.recover():           # its own reflex stopped it: another goal, not this one
                self.failed.append(goal); continue
            if res == 'SUCCEEDED':
                fails = 0; self.look_around(); continue
            self.failed.append(goal); fails += 1
            if self.boxed() and self.escape()[0]:             # it was planning from inside an obstacle's ring: out, on
                fails -= 1; continue
            if fails >= 5:
                if winds > 0:                                 # a second wind before giving up: fresh costmaps, on again
                    winds -= 1; fails = 0; self.clear_costmaps(); self.spin(3.0)
                    self.log('second_wind', left=winds)
                else:
                    reason = f'{5 * (SECOND_WINDS + 1)} goals failed over {SECOND_WINDS + 1} starts'
        self.log('stop', reason=reason, secs=round(time.time() - self.t0), driven_m=round(self.dist, 2))
        back = 'skipped'
        if not self.stop_reason() and home:
            back = self.go_home(home)
        p = self.pose()
        summary = dict(reason=reason, secs=round(time.time() - self.t0), driven_m=round(self.dist, 2), goals=self.goals,
                       home=back, start=home, end=p,
                       home_err_m=round(math.dist(p[:2], home[:2]), 2) if p and home else None, estops=self.estops,
                       stalls=len(self.stalls), looks=self.looks, escapes=self.escapes,
                       battery=round(self.battery() or 0, 2))
        json.dump(summary, open(os.path.join(self.dir, 'summary.json'), 'w'), indent=1)
        self.log('summary', **summary)
        self.speak_outcome(summary)
        self.clean = back == 'SUCCEEDED'                      # the map of a run that got home is worth keeping

    def speak_outcome(self, s):
        """Its own voice for the moments that need the owner (2026-10-05: it stood between chair legs for minutes, and
        later home without its charger, and said nothing): could not get home -> urgent; low battery -> urgent; home
        and not on the charger -> normal. selfcare/say.py queue; Buddy speaks it; the key stops repeats."""
        try:
            sys.path.insert(0, os.path.expanduser('~/selfcare'))
            from say import enqueue
            if str(s['reason']).startswith('low battery'):
                enqueue('battery_low', f"My battery is low ({s['battery']} volts). Please plug me in.", 'urgent', repeat_s=900)
            elif s['home'] not in ('SUCCEEDED', 'skipped'):
                where = f"about {s['home_err_m']:.1f} metres from home" if s.get('home_err_m') is not None else 'somewhere in the room'
                enqueue('stuck', f"I could not get back home: {s['reason']}. I am {where}. Could you help me?", 'urgent', repeat_s=600)
            elif s['home'] == 'SUCCEEDED' and not self.st.get('plugged'):
                enqueue('plug_me', 'I am back home. Please plug me in when you can.', 'normal', repeat_s=1800)
        except Exception as e:
            self.log('say_failed', err=str(e)[:120])


def main():
    rclpy.init(); r = Run()
    try:
        with mind_paused(reason='room explore'):
            r.run()
    finally:
        try: r.wheels(False)
        except Exception as ex: print('disable failed:', ex, flush=True)
        sys.stdout.flush()
        os._exit(0 if getattr(r, 'clean', False) else 3)


if __name__ == '__main__':
    main()
