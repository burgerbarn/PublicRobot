"""The robot mind (rosorin-mind.service): always running; the head watches for the owner and follows him, curious
when he's not there, asleep (arm released -> driver auto-tucks) when nobody has been around for a while.
Modes: PAUSED (someone else has the arm/wheels) -> SLEEP (camera only, low rate) <-> CURIOUS (slow looks) <-> TRACK.
Yields, never fights: pauses while the gamepad is not locked, wheels are enabled, an e-stop is latched, or
/rosorin_mind/enable was set false (scripts, Buddy). Never touches the arm while paused.
Arm motion only through arm_controller/joint_jog (board_driver: accel limit, limits, arm-model veto, timeout).
Servo temperatures checked every TEMP_EVERY s while awake: > TEMP_MAX C -> sleep (cool down).
Logs: ~/vision/mind/<UTC day>/events.jsonl + a frame per acquisition (self-training data).
Run: python mind.py (robot, ~/vision/venv, ROS sourced)."""
import glob, json, math, os, random, subprocess, sys, time

import cv2
import numpy as np
import PIL.Image
import rclpy
from rclpy.signals import SignalHandlerOptions
from control_msgs.msg import JointJog
from std_srvs.srv import SetBool, Trigger
from std_msgs.msg import String
from sensor_msgs.msg import BatteryState
from rclpy.qos import DurabilityPolicy, QoSProfile

import reappear
import world as W                                     # its own world model: Matt + objects on its room map (restored 2026-10-06)
from look_learner import LookLearner
from attention import GAIN, DEAD_DEG, VMAX, LOST_S, LOOK_S, SWEEP_V, PROMPTS, OBJECTS, TH, aim_point
from scan import Scan, make_detector
from think import Thinker

HERE = os.path.dirname(os.path.abspath(__file__))
SLEEP_AFTER = 120.0          # s curious without anyone -> sleep
SLEEP_FPS = 2.0              # detection rate while asleep
HOLD_FPS = 4.0               # detection rate while tracking and holding still (re-centring: every frame)
WAKE_HITS = 2                # consecutive sleep detections needed to wake
TEMP_EVERY, TEMP_MAX, COOL_S = 120.0, 55.0, 600.0
# body (fixed reflexes, owner 2026-10-01 "between a robot and a being"): follow-through when the owner leaves view,
# curious head tilt (wrist roll) on finding them. Decisions (where to look for them) are LEARNED: reappear.py.
FOLLOW_S, FOLLOW_V = 0.9, 0.9              # s, rad/s: keep turning the way they went, easing out
NEUTRAL = (500, 178)                       # pan, wrist pulses: relaxed, facing the room
LEARNED = os.path.join(HERE, 'learned_objects.json')   # owner-taught object names, e.g. ["a capo"]
LEARNED_TH = 0.2


def load_learned():
    try:
        return [str(x) for x in json.load(open(LEARNED))]
    except (OSError, ValueError):
        return []


# called but the owner isn't in view: look where the robot has LEARNED Matt usually is (presence map, built live from
# its own tracking: every 10 s on him -> +1 for that head direction); a room sweep only fills in while it knows little
PRESENCE = os.path.join(HERE, 'presence.json')
PBIN = 60                                  # pulses (~14 deg) per presence cell
EXPLORE_PTS = [(130, 178), (870, 178), (870, 260), (130, 260)]   # untrained fallback (exploration)
# "what do you see" = an inquisitive look around (owner 2026-10-01: the fixed 6-stop sweep was robotic): the head goes
# where something interesting is - new / changed / half out of frame / unknown things - centres it, dwells, keeps the
# close look; otherwise it glances at the least recently seen part of the room. Room memory (what was where, when)
# makes the next look-around go after what changed. Arm only; ~25 s; up to 6 close looks for Gemma to identify.
ROOMSCANS = os.path.join(HERE, 'roomscans')
ROOM_MEM = os.path.join(HERE, 'room_memory.json')     # {"pan_cell,wrist_cell": {"t": last look, "labels": {l: t}}}
BODY_SKILLS = {'practice_moving': 'rosorin-practice', 'explore_room': 'rosorin-explore'}   # its own wheel skills
BODY_EVERY, BODY_MIN_V, READY_MOVES = 900.0, 10.6, 60     # s between wheel skills, battery floor, practice before exploring
PRACTICE_EVERY = 3600.0    # s: plugged in and alone, it practises looking this often (owner 2026-10-07: every hour, was 5 min)
CAM_STALE_S, CAM_RETRY_S = 20.0, 120.0   # 2026-10-07: camera "running" but silent after a boot -> restart it itself
SCAN_S, MAX_LOOKS, CELL = 25.0, 6, 70                 # s, close looks, pulses per memory cell (~17 deg)
PAN_RANGE, WRIST_ROWS = (150, 850), (150, 178, 230)   # where it wanders (pan pulses), head tilt rows (pulses)
RAD_PER_PULSE = 0.0041887902047863905
INSPECT_S = 5.0
INSPECT_MAX_DEG = 15.0                     # 'what am I holding' adjusts at most this far from the current view                            # s looking at the held object after "what do you see"
TILT_PULSES, TILT_S = 85, 1.6              # curious head tilt ~20 deg (owner: 8 deg too muted), held this long
HOLD_DEG, SETTLE_DEG, ERR_ALPHA = 10.0, 2.0, 0.4    # track: hold zone, re-centre target, error smoothing
FACE_EVERY, FACE_CAP = 5.0, 500            # while tracking a face: a frame every 5 s for self-training, cap/day
GLANCE_EVERY, GLANCE_S = 3600.0, 20.0     # idle glance: owner 2026-10-07 every hour (was 5 min)
def _owner_limits(path=os.path.expanduser('~/ros2_ws/src/rosorin_base/config/arm_limits.yaml')):
    """The owner's mapped servo limits (one source of truth; the driver enforces the same file + its model veto)."""
    import yaml
    p = yaml.safe_load(open(path))['rosorin_board_driver']['ros__parameters']
    return {k: tuple(p[f'arm_limit_{k}']) for k in (1, 4)}
LIM = _owner_limits()
# owner 2026-10-01: the mind used its own 150-280 tilt range (31 deg of the owner's 121-935); now the owner's limits
WRIST_LIM, PAN_LIM = LIM[4], LIM[1]
# 2026-10-05 the rest pose changed (owner placed it: joint 2 776, joint 3 211): the same view angle is now 65 wrist
# pulses lower (model: +15.6 deg at equal wrist), and the wrist limit caps how far down it can look (-4 deg).
REST_SHIFT = -65
_w = lambda w: max(WRIST_LIM[0], w + REST_SHIFT)
NEUTRAL = (NEUTRAL[0], _w(NEUTRAL[1]))
EXPLORE_PTS = [(p, _w(w)) for p, w in EXPLORE_PTS]
WRIST_ROWS = tuple(sorted({_w(w) for w in WRIST_ROWS}))
THINK_WAKE_EVERY = 3600.0                  # a thought may wake the arm at most every hour (owner 2026-10-07; was 10 min), only if the scene changed     # asleep: wake for a short look around every 5 min (one camera direction misses you)


def inspect_point(dets):
    """(u, v, what) of what the owner is holding: the best non-person object nearest a hand, else the hand."""
    hands = [d for d in dets if d[0] == 'a hand']
    objs = [d for d in dets if d[0] not in ('a face', 'a person', 'a hand')]
    c = lambda b: ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    if objs and hands:
        o = min(objs, key=lambda d: min(math.dist(c(d[2]), c(h[2])) for h in hands) - 100 * d[1])
        return (*c(o[2]), 'held ' + o[0])
    if hands:
        h = max(hands, key=lambda d: d[1]); return (*c(h[2]), 'hand')
    if objs:
        o = max(objs, key=lambda d: d[1]); return (*c(o[2]), o[0])
    return None


class Mind:
    def __init__(self):
        self.n = Scan()
        n = self.n
        # the driver e-stops on >1 arm/goal publisher: the mind moves only via joint_jog, so drop Scan's publisher
        n.destroy_publisher(n.goal_pub); n.goal_pub = None
        self.jog = n.create_publisher(JointJog, 'arm_controller/joint_jog', 10)
        for name in ('arm/recover', 'arm/read_state'):
            n.cli[name] = n.create_client(Trigger, f'/rosorin_board_driver/{name}')
        self.enabled = True
        n.create_service(SetBool, '/rosorin_mind/enable', self.on_enable)
        self.mode_pub = n.create_publisher(String, '/rosorin_mind/mode', QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.detect = make_detector('nanoowl', '32', PROMPTS + OBJECTS)
        self.detect.add_labels(load_learned())                # objects the owner taught (robot_eyes teach_robot)
        self.detect.add_labels(['an object'])                 # unknown things can draw interest in a look-around
        self.mode, self.why = 'sleep', 'start'
        self.last_seen = self.last_person = 0.0
        self.awake_since = time.time()
        self.sweep_dir, self.sweep_until, self.pause_until = 1, 0.0, 0.0
        self.wake_hits, self.last_det, self.next_temp, self.cool_until = 0, 0.0, 0.0, 0.0
        self.next_glance, self.glance_until = time.time() + GLANCE_EVERY, 0.0
        self.have_arm = False
        self.day, self.log = None, None
        self.hb_t, self.hb_frames, self.hb_seen = time.time(), 0, 0
        self.thinker = Thinker()                              # the robot's own thinking (on-board VLM), optional
        self.steer = None                                     # a head direction the thinker chose {'pan','wrist','until'}
        self.lost = None                                      # after losing the owner: follow-through / look / wait
        self.roll_tgt, self.roll_until = 500, 0.0
        self.looks, self.next_practice = LookLearner(), time.time() + PRACTICE_EVERY   # what its looks are worth, learned
        self.body = None                                      # a wheel skill it started: {'skill', 'unit', 't0'}
        self.battery_v = None
        n.create_subscription(BatteryState, '/battery', lambda m: setattr(
            self, 'battery_v', m.voltage if self.battery_v is None else 0.9 * self.battery_v + 0.1 * m.voltage), 5)
        n.cli['reset_estop'] = n.create_client(Trigger, '/rosorin_board_driver/reset_estop')
        self.reflex_reset_t = 0.0
        self.nav_status = {}                                  # navigation's own word: placed, servers up, planner answers (nav_supervisor)
        n.create_subscription(String, '/nav/status', lambda m: setattr(self, 'nav_status', json.loads(m.data)),
                              QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.body_next, self.stay_until, self.know_t, self.know = 0.0, 0.0, 0.0, {}
        self.reappear = reappear.load()                       # learned nightly from this robot's own lost/found events
        self.world = W.World()                                # where Matt and the things of the room are, on its map (~/vision/world.json)
        self.world_matt_t = self.world_obj_t = 0.0
        # Buddy's ears (bigbuddy mic -> robot_api /heard): "yo buddy" wakes the robot / gets a tilt; speech feeds thinking
        self.heard_q, self.said, self.owner_pose = [], None, None
        n.create_subscription(String, '/buddy/heard', lambda m: self.heard_q.append(json.loads(m.data)), 10)
        self.seen_pub = n.create_publisher(String, '/rosorin_mind/seen', 2)
        self.inspect_until = 0.0
        self.search, self.inspect_pending = None, False
        self.room_scan = None
        try:
            self.presence = json.load(open(PRESENCE))         # {"pan,wrist": count}
        except (OSError, ValueError):
            self.presence = {}

    # ---------- plumbing
    def on_enable(self, req, res):
        self.enabled = req.data
        res.success, res.message = True, f'mind {"enabled" if req.data else "disabled"}'
        self.event('enable', on=req.data)
        return res

    def event(self, kind, **kw):
        day = time.strftime('%Y%m%d', time.gmtime())
        if day != self.day:
            self.day = day
            self.faces_today = 0
            self.dir = os.path.join(HERE, 'mind', day); os.makedirs(self.dir, exist_ok=True)
            self.log = open(os.path.join(self.dir, 'events.jsonl'), 'a')
        self.log.write(json.dumps({'t': round(time.time(), 3), 'event': kind, 'mode': self.mode, **kw}) + '\n')
        self.log.flush()

    def set_mode(self, mode, why):
        if mode == 'sleep':
            self.lost, self.search, self.room_scan = None, None, None
        if mode != self.mode:
            print(f'{time.strftime("%H:%M:%S")} {self.mode} -> {mode} ({why})', flush=True)
            self.event('mode', to=mode, why=why)
            self.mode, self.why = mode, why
        self.publish_mode()

    def publish_mode(self):
        self.mode_pub.publish(String(data=json.dumps({'mode': self.mode, 'why': self.why, 'enabled': self.enabled,
                                                      'have_arm': self.have_arm})))

    def send(self, v1, v4):
        # head tilt stays in the model-checked range (2026-09-30: tracking tilted it to wrist 497 = staring at the
        # ceiling for minutes). joint4 +rad = wrist pulse DOWN. Outside the range: only motion back toward it.
        wr = ((self.n.state() or {}).get('arm_pose') or {}).get('4', 178)
        if wr >= WRIST_LIM[1]:
            v4 = max(v4, 0.15 if wr > WRIST_LIM[1] + 5 else 0.0)
        elif wr <= WRIST_LIM[0]:
            v4 = min(v4, -0.15 if wr < WRIST_LIM[0] - 5 else 0.0)
        # wrist roll: always sent (the driver keeps each joint's last command); returns to level after a tilt
        pose = (self.n.state() or {}).get('arm_pose') or {}
        r, rt = pose.get('5', 500), (self.roll_tgt if time.time() < self.roll_until else 500)
        v5 = 0.0 if abs(r - rt) <= 3 else max(-1.1, min(1.1, 0.012 * (r - rt)))   # joint +rad = pulse down
        m = JointJog(joint_names=['joint1', 'joint4', 'joint5'], velocities=[float(v1), float(v4), float(v5)])
        m.header.stamp = self.n.get_clock().now().to_msg()
        self.jog.publish(m)

    def go_to(self, pan, wr, tpan, twr, gain, vmax):
        """Ease the head toward (tpan, twr) pulses: proportional = fast start, soft landing."""
        v1 = 0.0 if abs(pan - tpan) <= 6 else max(-vmax, min(vmax, gain * (pan - tpan)))   # joint +rad = pulse down
        v4 = 0.0 if abs(wr - twr) <= 6 else max(-vmax, min(vmax, gain * (wr - twr)))
        self.send(v1, v4)

    def take_arm(self):
        """recover (untuck) if needed, then enable; False if the driver refuses."""
        n = self.n
        st = n.state() or {}
        if not st.get('arm_holding'):
            r = n.call('arm/recover', Trigger.Request())
            if not r.success:
                self.arm_refused_t = time.time(); self.event('arm_refused', step='recover', msg=r.message[:120]); return False
        if time.time() < getattr(self, 'arm_retry_at', 0.0):
            return False
        r = n.call('arm/enable', SetBool.Request(data=True))
        if not r.success and 'outside limits' in r.message:
            # holding but sagged a few pulses past a limit (2026-10-01: servo 3 at 36 vs 40): one slow recover move
            # brings it back inside, then enable
            rr = n.call('arm/recover', Trigger.Request())
            self.event('arm_recover_sag', ok=rr.success, msg=rr.message[:120])
            if rr.success:
                r = n.call('arm/enable', SetBool.Request(data=True))
        if not r.success:
            self.arm_retry_at = time.time() + 30.0             # was retried every second (log flood, wasted effort)
            self.arm_refused_t = time.time(); self.event('arm_refused', step='enable', msg=r.message[:120]); return False
        n.spin_until(lambda: (n.state() or {}).get('arm_enabled'), 5, 'arm enabled')
        self.have_arm = True
        return True

    def release_arm(self):
        if self.have_arm:
            self.send(0.0, 0.0); time.sleep(0.4)
            self.n.call('arm/enable', SetBool.Request(data=False))     # holding -> driver auto-tucks after 2 min
            self.have_arm = False

    def hot(self):
        try:
            st = json.loads(self.n.call('arm/read_state', Trigger.Request()).message or '{}')
            t = {k: v.get('temp_c') for k, v in st.items() if isinstance(v, dict)}
            self.event('temps', temps=t)
            return max(v for v in t.values() if v is not None) > TEMP_MAX
        except Exception as e:
            self.event('temps_failed', err=str(e)[:100])
            return False

    # ---------- main loop
    def camera_watch(self, now):
        """2026-10-07: after a boot the camera service ran but published no frame for 23 min; every step returned
        silently, so it could not look for the owner and nobody knew. No new frame for CAM_STALE_S -> restart the
        camera (its own heal, at most once per CAM_RETRY_S, logged); still blind after a second try -> tell the owner."""
        m = self.n.last.get('rgb')
        stamp = None if m is None else (m.header.stamp.sec, m.header.stamp.nanosec)
        if not hasattr(self, 'cam_t') or stamp != self.cam_stamp:
            if hasattr(self, 'cam_t') and getattr(self, 'cam_restarts', 0):
                self.event('camera_back', after_restarts=self.cam_restarts)
            self.cam_stamp, self.cam_t, self.cam_restarts = stamp, now, 0
            return
        if now - self.cam_t < CAM_STALE_S or now - getattr(self, 'cam_fix_t', 0.0) < CAM_RETRY_S:
            return
        self.cam_fix_t = now; self.cam_restarts += 1
        self.event('camera_silent', secs=round(now - self.cam_t), restart=self.cam_restarts)
        subprocess.run(['sudo', 'systemctl', 'restart', '--no-block', 'rosorin-camera'], capture_output=True)
        if self.cam_restarts >= 2:
            try:
                sys.path.insert(0, os.path.expanduser('~/selfcare'))
                from say import enqueue
                enqueue('camera_blind', 'My camera is not sending pictures, even after restarting it. I cannot see.',
                        'urgent', repeat_s=1800)
            except Exception as e:
                self.event('say_failed', err=str(e)[:100])

    def step(self):
        n = self.n
        st = n.state() or {}
        now = time.time()
        self.body_step(now)                                   # a wheel skill it started: has it finished?
        self.camera_watch(now)                                # its eyes: are frames arriving at all?
        for ev in [e for e in self.heard_q if e.get('event') == 'speech']:     # the owner's stop word counts even
            if set(ev.get('text', '').lower().replace(',', ' ').replace('.', ' ').split()) & {'stop', 'stay', 'halt', 'freeze'}:
                self.stop_body(ev.get('text', '')[:60], now)                   # while the mind is paused by a skill
        if st.get('estop') == 'estop topic' and self.body is None and now - self.reflex_reset_t > 20.0:
            # its own contact reflex stopped it and the skill has ended: clear its own stop so it can go on living
            # (a gamepad e-stop is the owner's and is never cleared here)
            self.reflex_reset_t = now
            try:
                self.n.call('reset_estop', Trigger.Request()); self.event('reflex_stop_cleared')
            except Exception as ex:
                self.event('reflex_clear_error', err=str(ex)[:100])
        busy = (not self.enabled and 'disabled') or (st.get('estop') and 'e-stop') or \
               (st.get('enabled') and 'wheels enabled') or (st.get('pad_mode') not in (None, 'locked') and 'gamepad in use')
        if busy:
            if self.mode != 'paused':
                if busy == 'disabled':
                    self.release_arm()                        # deliberate handoff (scripts/Buddy): give the arm back
                else:
                    self.send(0.0, 0.0)
                    self.have_arm = False                     # gamepad/wheels/e-stop own it now: don't touch
            self.set_mode('paused', busy)
            return
        if self.mode == 'paused':
            self.set_mode('sleep', 'resume'); self.last_det = 0.0
        if self.heard_q:
            while self.heard_q:
                self.on_heard(self.heard_q.pop(0), time.time(), st)
            st = n.state() or {}                              # fresh: on_heard may just have taken the arm
            if self.have_arm and not st.get('arm_enabled'):
                st = dict(st, arm_enabled=True)               # enable is in flight; don't call it 'released'
        if self.have_arm and not st.get('arm_enabled'):       # driver dropped it (auto-tuck, disable elsewhere)
            self.have_arm = False
            if self.mode in ('curious', 'track'):
                self.set_mode('sleep', 'arm released by driver')
        m = n.last.get('rgb')
        if m is None or m.header.stamp == getattr(self, 'stamp', None):
            return
        if self.mode == 'sleep' and now - self.last_det < 1.0 / SLEEP_FPS:
            return
        # efficiency (owner 2026-10-01): holding still on the owner needs no 14 fps; full rate only while re-centring
        if self.mode == 'track' and not getattr(self, 'recentring', False) and now - self.last_det < 1.0 / HOLD_FPS:
            return
        self.stamp, self.last_det = m.header.stamp, now
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)
        rgb = img[:, :, ::-1] if m.encoding == 'bgr8' else img
        dets = [d for d in self.detect(PIL.Image.fromarray(rgb), 0.12) if d[1] >= TH.get(d[0], LEARNED_TH)]
        ap = aim_point(dets)
        if ap:
            self.last_person = now
        if now - getattr(self, 'seen_pub_t', 0.0) >= 0.5:      # what the robot sees, for Buddy (robot_api /seen)
            self.seen_pub_t = now
            m_ = self.world.matt(max_age=86400)
            self.seen_pub.publish(String(data=json.dumps({'t': now, 'mode': self.mode, 'inspecting': now < self.inspect_until,
                'owner_last': None if not m_ else dict(x=round(m_['x'], 2), y=round(m_['y'], 2), age_s=int(now - m_['t'])),
                'dets': [(d[0], round(float(d[1]), 3), [int(x) for x in d[2]]) for d in dets]})))
        if ap and now < self.inspect_until:                   # "what am I holding": from where it already looks
            ip = inspect_point(dets)                          # a hand / object in THIS view, else no move at all
            K_ = np.array(n.last['info'].k).reshape(3, 3)
            if ip and abs(math.degrees(math.atan((ip[0] - K_[0, 2]) / K_[0, 0]))) < INSPECT_MAX_DEG and \
                    abs(math.degrees(math.atan((ip[1] - K_[1, 2]) / K_[1, 1]))) < INSPECT_MAX_DEG:
                ap = ip
                self.recentring = True                         # small adjustment onto it
            else:
                self.recentring, self.f_err = False, None      # hold still: the picture is taken from here
                self.send(0.0, 0.0); self.last_seen = now; return
        pose = st.get('arm_pose') or {}
        small = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), (64, 48)).astype(np.float32)
        ref = getattr(self, 'scene_ref', None)
        change = float(np.abs(small - ref).mean()) if ref is not None else 0.0
        if ref is None or now - getattr(self, 'scene_ref_t', 0) > 60:
            self.scene_ref, self.scene_ref_t = small, now          # compare with the scene ~1 min ago
        self.thinker.feed(rgb, {'mode': self.mode, 'doing': self.why, 'scene_change': round(change, 1),
                                'I_see': sorted({d[0].replace('a ', '') for d in dets}) or ['nothing specific'],
                                'owner_last_seen_s_ago': round(now - self.last_person) if self.last_person else None,
                                'owner_said': (self.said['text'] if self.said and now - self.said['t'] < 60 else None),
                                'head_turn_deg': round(1.0 + 0.24 * (pose.get('1', 500) - 500)),
                                **self.self_knowledge(now, st),
                                'local_time': time.strftime('%H:%M')}, asleep=self.mode == 'sleep')
        self.apply_thought(now, st)
        self.hb_frames += 1; self.hb_seen += bool(ap)
        if now - self.hb_t >= 60:
            self.event('heartbeat', frames=self.hb_frames, saw_you=self.hb_seen, pan=(st.get('arm_pose') or {}).get('1'))
            self.hb_t, self.hb_frames, self.hb_seen = now, 0, 0

        if self.mode == 'sleep':
            self.wake_hits = self.wake_hits + 1 if ap else 0
            if self.wake_hits >= WAKE_HITS and now >= self.cool_until:
                self.wake_hits = 0
                if self.take_arm():
                    self.awake_since, self.next_temp = now, now + TEMP_EVERY
                    self.set_mode('track', f'saw you ({ap[2]})')
                    self.acquire(rgb, ap, dets, now)
            elif st.get('plugged') and now >= self.next_practice and now >= self.cool_until:
                # on the charger and alone: practise looking - its own inquisitive look-around, learning from each look
                self.next_practice = now + PRACTICE_EVERY
                self.on_heard({'event': 'scan_room', 'why': 'practice'}, now, st)
            elif now >= self.next_glance and now >= self.cool_until:
                self.next_glance = now + GLANCE_EVERY
                if self.take_arm():
                    self.awake_since, self.next_temp, self.glance_until = now, now + TEMP_EVERY, now + GLANCE_S
                    self.pause_until, self.sweep_until = 0.0, 0.0
                    self.set_mode('curious', 'glance around')
            return

        # awake (curious / track)
        if now >= self.next_temp:
            self.next_temp = now + TEMP_EVERY
            if self.hot():
                self.release_arm(); self.cool_until = now + COOL_S
                self.set_mode('sleep', f'servos over {TEMP_MAX} C: cooling {COOL_S:.0f} s'); return
        K = np.array(n.last['info'].k).reshape(3, 3)
        if self.room_scan:
            self.room_scan_step(now, st, rgb, dets, K); return
        if now - self.world_obj_t > 2.0:                       # what it sees goes into its world model
            self.world_obj_t = now
            for d in dets:
                if d[0] not in ('a face', 'a person', 'a hand') and d[1] >= 0.3:
                    xy = W.locate(n.tf, n.last.get('scan'), (d[2][0] + d[2][2]) / 2, K)
                    if xy is not None:
                        self.world.see_object(d[0], xy)
            self.world.save()
        if ap:
            if self.mode != 'track':
                self.set_mode('track', f'found you ({ap[2]})')
                self.acquire(rgb, ap, dets, now)
            if ap[2] in ('face', 'person') and now - self.world_matt_t > 1.0:
                self.world_matt_t = now
                xy = W.locate(n.tf, n.last.get('scan'), ap[0], K)
                if xy is not None:
                    self.world.see_matt(xy); self.world.save()
            u, v, w = ap
            rx = math.atan((u - K[0, 2]) / K[0, 0]); ry = math.atan((v - K[1, 2]) / K[1, 1])
            # owner 2026-10-01: "moving all over the place ... wasted energy". The aim point flips face <-> person
            # (4-6 deg apart, log) and the 1.5 deg dead zone chased every flicker. Now: smoothed error + hold still
            # while the target is within HOLD_DEG; once it leaves, re-centre until within SETTLE_DEG, then hold.
            fe = getattr(self, 'f_err', None)
            self.f_err = (rx, ry) if fe is None or now - self.last_seen > LOST_S else \
                (fe[0] + ERR_ALPHA * (rx - fe[0]), fe[1] + ERR_ALPHA * (ry - fe[1]))
            ex, ey = self.f_err
            # tilt at the owner's limit (WRIST_LIM, enforced in send) can't close the vertical error: count that axis as done,
            # else re-centring never ends and the arm keeps pushing (2026-10-01: wrist 280, owner standing above it)
            wr = ((n.state() or {}).get('arm_pose') or {}).get('4', 178)
            if (wr >= WRIST_LIM[1] and ey < 0) or (wr <= WRIST_LIM[0] and ey > 0): ey = 0.0
            off = math.degrees(math.hypot(ex, ey))
            rc = getattr(self, 'recentring', False)
            self.recentring = (off > SETTLE_DEG) if rc else (off > HOLD_DEG)
            if self.recentring:
                v1 = max(-VMAX, min(VMAX, GAIN * ex)); v4 = max(-VMAX, min(VMAX, GAIN * ey))
            else:
                v1 = v4 = 0.0
                p_ = st.get('arm_pose') or {}
                self.owner_pose = (p_.get('1', 500), p_.get('4', 178))     # centred on them: where they are
                if now - getattr(self, 'presence_t', 0.0) >= 10.0:            # learn where Matt usually is
                    self.presence_t = now
                    k = f'{self.owner_pose[0] // PBIN},{self.owner_pose[1] // PBIN}'
                    self.presence[k] = self.presence.get(k, 0) + 1
                    try:
                        json.dump(self.presence, open(PRESENCE + '.tmp', 'w')); os.replace(PRESENCE + '.tmp', PRESENCE)
                    except OSError:
                        pass
            self.send(v1, v4)
            self.last_seen = now
            self.glance_until = 0.0
            if w == 'face' and now - getattr(self, 'last_face_save', 0) > FACE_EVERY and getattr(self, 'faces_today', 0) < FACE_CAP:
                self.last_face_save = now; self.faces_today = getattr(self, 'faces_today', 0) + 1
                cv2.imwrite(os.path.join(self.dir, time.strftime('%H%M%S', time.gmtime()) + '_face.jpg'),
                            cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))           # owner_faces.py learns from these
            if random.random() < 0.1:                         # sampled: enough for tuning, not a flood
                self.event('track', what=w, err_deg=[round(math.degrees(rx), 1), round(math.degrees(ry), 1)],
                           smooth_deg=[round(math.degrees(ex), 1), round(math.degrees(ey), 1)], moving=self.recentring)
            return
        if self.mode == 'track' and now - self.last_seen < LOST_S:
            gone = now - self.last_seen                       # follow-through: keep turning the way they went, easing out
            fe = getattr(self, 'f_err', None) or (0.0, 0.0)
            if 0.4 < gone < 0.4 + FOLLOW_S and abs(math.degrees(fe[0])) > 3.0:
                p = (gone - 0.4) / FOLLOW_S; self.send((1 if fe[0] > 0 else -1) * FOLLOW_V * (1 - p) ** 2, 0.0)
            else:
                self.send(0.0, 0.0)
            return
        if self.mode == 'track':
            self.set_mode('curious', 'lost you'); self.pause_until, self.sweep_until = now + 1.0, 0.0
            pose = st.get('arm_pose') or {}
            fe = getattr(self, 'f_err', None) or (0.0, 0.0)
            side = 1 if fe[0] >= 0 else -1                    # which edge of the view they left by
            self.lost = {'t0': now, 'side': side, 'pan': pose.get('1', 500), 'wrist': pose.get('4', 178)}
            self.event('lost', side=side, pan=self.lost['pan'], wrist=self.lost['wrist'])
            # follow-through counts from when they actually left (LOST_S ago), not from now
            self.lost['t0'] = self.last_seen
        if self.glance_until and now > self.glance_until and now - max(self.last_person, self.awake_since) > GLANCE_S:
            self.glance_until = 0.0
            self.release_arm(); self.set_mode('sleep', 'glance: nobody'); return
        if now - max(self.last_person, self.awake_since) > SLEEP_AFTER:     # from waking, not from long ago
            self.release_arm(); self.set_mode('sleep', f'nobody for {SLEEP_AFTER:.0f} s'); return
        if self.search and self.mode == 'curious':
            S, pose = self.search, st.get('arm_pose') or {}
            pan, wr = pose.get('1', 500), pose.get('4', 178)
            tp, tw = S['pts'][S['i']]
            if abs(pan - tp) <= 10 and abs(wr - tw) <= 10 or now - S['t'] > 6.0:   # reached (or stuck): next point
                S['i'] += 1; S['t'] = now
                if S['i'] >= len(S['pts']):
                    self.search, self.inspect_pending = None, False
                    self.event('search_done', found=False); self.send(0.0, 0.0); return
            else:
                self.go_to(pan, wr, tp, tw, gain=0.005, vmax=0.8)
            return
        if self.lost and self.mode == 'curious':
            self.lost_step(now, st); return
        pan = (st.get('arm_pose') or {}).get('1', 500)
        if self.steer and now < self.steer['until']:              # the thinker chose where to look
            wr = (st.get('arm_pose') or {}).get('4', 178)
            v1 = max(-0.4, min(0.4, 0.004 * (pan - self.steer['pan'])))      # joint1 +rad = pan pulse down
            v4 = max(-0.3, min(0.3, 0.004 * (wr - self.steer['wrist'])))     # joint4 +rad = wrist pulse down
            self.send(v1 if abs(pan - self.steer['pan']) > 6 else 0.0, v4 if abs(wr - self.steer['wrist']) > 6 else 0.0)
            return
        self.steer = None
        if pan < 200: self.sweep_dir = -1
        if pan > 800: self.sweep_dir = 1
        if now < self.pause_until:
            self.send(0.0, 0.0)
        elif now < self.sweep_until:
            self.send(self.sweep_dir * SWEEP_V, 0.0)
        else:
            self.sweep_until = now + random.uniform(1.0, 2.5); self.pause_until = self.sweep_until + LOOK_S
            if random.random() < 0.3:
                self.sweep_dir = -self.sweep_dir

    def self_knowledge(self, now, st):
        """What the robot knows about itself, for its own decisions: can it drive now, what has it learned so far."""
        if now - self.know_t > 20.0:
            self.know_t = now
            k = {}
            try:
                bm = json.load(open(os.path.expanduser('~/practice/body_model.json'))); k['practice_moves_done'] = bm.get('trials', 0)
            except (OSError, ValueError):
                k['practice_moves_done'] = 0
            k['head_directions_learned'] = len(self.looks.model.get('looks', {}))
            k['have_room_map'] = os.path.exists(os.path.expanduser('~/maps/room_live.yaml'))
            self.know = k
        if now - getattr(self, 'units_t', 0.0) > 5.0:          # a wheel skill running that it did not start (a script,
            self.units_t = now                                # Buddy's home/come task, a look-around pause in a drive)
            out = subprocess.run(['systemctl', 'is-active', *BODY_SKILLS.values(), 'rosorin-task@home', 'rosorin-task@come',
                                  'rosorin-task@sethome'], capture_output=True, text=True).stdout.split()
            self.wheel_skill_running = any(x in ('active', 'activating') for x in out)
        b = getattr(self, 'battery_v', None)
        nav_ready = bool(self.nav_status.get('ready')) and (time.time() - self.nav_status.get('t', 0)) < 30
        silent = list(st.get('servo_silent') or [])
        # 2026-10-06: it must know what it cannot do right now (review findings 40-42: it chose look_around and
        # practice_looking with a refused arm, and explore with navigation deaf). Facts from its own services, no rule of mine.
        can = (not st.get('plugged')) and st.get('estop') is None and (b is None or b >= BODY_MIN_V) and \
            now >= self.stay_until and now >= self.body_next and self.body is None and nav_ready and \
            not self.wheel_skill_running
        return dict(self.know, plugged=bool(st.get('plugged')), battery_v=None if b is None else round(b, 2),
                    can_drive=bool(can), ready_to_explore=self.know.get('practice_moves_done', 0) >= READY_MOVES,
                    owner_said_stay=now < self.stay_until, navigation_ready=nav_ready,
                    wheel_skill_running=self.wheel_skill_running,
                    owner_last_seen=(lambda m: None if not m else dict(x=round(m['x'], 2), y=round(m['y'], 2), age_s=int(now - m['t'])))(self.world.matt(max_age=86400)),
                    my_place_on_map=(lambda p: None if not p else [round(p[0], 2), round(p[1], 2)])(W.robot_pose(self.n.tf)),
                    arm_servos_not_answering=silent, arm_refused=bool(getattr(self, 'arm_refused_t', 0) > now - 60))

    def start_body_skill(self, skill, now, st, why):
        """The robot starts one of its own wheel skills (its own service; the mind hands over the arm and waits).
        Refused, with the reason logged, unless it can drive now; exploring also needs its body practice done."""
        k = self.self_knowledge(now, st)
        if not k['can_drive'] or (skill == 'explore_room' and not k['ready_to_explore']):
            self.event('body_skill_refused', skill=skill, state={x: k[x] for x in ('can_drive', 'plugged', 'battery_v', 'ready_to_explore', 'owner_said_stay')}); return False
        unit = BODY_SKILLS[skill]
        if self.have_arm:
            self.release_arm()
        r = subprocess.run(['sudo', 'systemctl', 'start', '--no-block', unit], capture_output=True, text=True)
        self.body = {'skill': skill, 'unit': unit, 't0': now}
        self.body_next = now + BODY_EVERY
        self.event('body_skill_started', skill=skill, why=why, ok=r.returncode == 0)
        return r.returncode == 0

    def body_step(self, now):
        """A wheel skill it started has ended: log how it went (its own experience of deciding)."""
        B = self.body
        if B is None or now - B['t0'] < 15 or now - B.get('checked', 0) < 5:
            return
        B['checked'] = now
        if subprocess.run(['systemctl', 'is-active', '--quiet', B['unit']]).returncode == 0:
            return
        out = {}
        try:
            d = max(glob.glob(os.path.expanduser('~/explore/*/')), key=os.path.getmtime)
            if B['skill'] == 'explore_room' and os.path.getmtime(d) > B['t0']:
                out = json.load(open(os.path.join(d, 'summary.json')))
        except (ValueError, OSError):
            pass
        self.know_t = 0.0
        self.event('body_skill_done', skill=B['skill'], secs=round(now - B['t0']), result={k: out.get(k) for k in ('reason', 'driven_m', 'home', 'stalls')} if out else None)
        self.body = None

    def stop_body(self, why, now, stay_s=1800.0):
        """The owner said stop / stay: end any wheel skill and stay put for a while."""
        for unit in BODY_SKILLS.values():
            subprocess.run(['sudo', 'systemctl', 'stop', '--no-block', unit], capture_output=True)
        self.stay_until = now + stay_s
        self.event('owner_stop', why=why, stay_s=stay_s)

    def apply_thought(self, now, st):
        got = self.thinker.take()
        if not got:
            return
        _, d = got
        skill, args = d.get('skill'), d.get('args') or {}
        self.event('thought', skill=skill, args=args, thought=d.get('thought'), conf=d.get('confidence'), latency=d.get('latency_s'))
        print(f'{time.strftime("%H:%M:%S")} thinks: {d.get("thought")} -> {skill} {args}', flush=True)
        if skill in BODY_SKILLS:                              # its own decision to use its wheels
            self.start_body_skill(skill, now, st, d.get('thought')); return
        if skill == 'practice_looking' and self.mode != 'track' and self.room_scan is None:
            self.on_heard({'event': 'scan_room', 'why': 'practice'}, now, st); return
        if self.mode == 'track' or skill in ('keep_watching', 'identify', 'ask_owner'):
            return                                            # tracking the owner wins; notes/questions are logged
        pose = st.get('arm_pose') or {}
        pan, wr = pose.get('1', 500), pose.get('4', 178)
        if skill == 'rest':
            if self.mode == 'curious':
                self.release_arm(); self.set_mode('sleep', 'decided to rest')
            return
        if self.mode == 'sleep':                              # wake up to look (same path as a glance)
            changed = (self.thinker.state or {}).get('scene_change', 0) >= 8.0
            if now - getattr(self, 'last_think_wake', 0) < THINK_WAKE_EVERY or not changed:
                self.event('thought_wake_skipped', why='too soon' if changed else 'nothing changed'); return
            if now < self.cool_until or not self.take_arm():
                return
            self.last_think_wake = now
            self.awake_since, self.next_temp, self.glance_until = now, now + TEMP_EVERY, now + GLANCE_S
            self.set_mode('curious', f'decided to {skill}')
        if skill == 'look_around':
            self.steer, self.sweep_until, self.pause_until = None, 0.0, 0.0
        elif skill == 'look_toward':
            dirn = str(args.get('direction', 'center')).lower()
            goal = {'left': (min(850, pan + 150), wr), 'right': (max(150, pan - 150), wr), 'up': (pan, min(_w(260), wr + 40)),
                    'down': (pan, max(_w(150), wr - 40)), 'center': NEUTRAL}.get(dirn, (pan, wr))
            self.steer = {'pan': goal[0], 'wrist': goal[1], 'until': now + 4.0}

    def on_heard(self, ev, now, st):
        """Buddy's microphone (bigbuddy): no direction yet (one capsule; the reSpeaker on the robot adds it later)."""
        self.event('heard', what=ev.get('event'), text=ev.get('text', '')[:80])
        if ev.get('event') == 'wake' and now - ev.get('t', now) > 5.0:
            return                                            # stale (queued while paused)
        if ev.get('event') == 'speech':
            self.said = {'text': ev.get('text', ''), 't': now}
            words = set(self.said['text'].lower().replace(',', ' ').replace('.', ' ').split())
            if words & {'stop', 'stay', 'halt', 'freeze'}:   # the owner's word ends any wheel skill
                self.stop_body(self.said['text'][:60], now)
            return
        if ev.get('event') == 'learn':                        # owner named an object: detect it from now on
            name = ev.get('text', '').strip().lower()
            name = name if name.startswith(('a ', 'an ', 'the ')) else ('an ' if name[:1] in 'aeiou' else 'a ') + name
            added = self.detect.add_labels([name])
            if added:
                json.dump(load_learned() + added, open(LEARNED, 'w'))
            self.event('learned', label=name, new=bool(added)); return
        if ev.get('event') == 'scan_room':                    # "what do you see": look around the room, arm only
            if self.mode == 'sleep':
                if now < self.cool_until or not self.take_arm():
                    return
                self.awake_since, self.next_temp = now, now + TEMP_EVERY
            self.set_mode('curious', 'looking around the room')
            self.looks.new_session()
            self.room_scan = {'t0': now, 'target': None, 'arrive': None, 'dwell': 0.0, 'shots': [], 'looked': [],
                              'dir': os.path.join(ROOMSCANS, time.strftime('%Y%m%d_%H%M%S'))}
            try:
                self.room_mem = json.load(open(ROOM_MEM))
            except (OSError, ValueError):
                self.room_mem = {}
            os.makedirs(self.room_scan['dir'], exist_ok=True)
            self.lost, self.search = None, None
            self.event('room_scan_start'); return
        if ev.get('event') == 'inspect':                      # Buddy asks what the robot sees: look at their hands
            self.inspect_until = now + INSPECT_S
            if self.mode == 'track':
                return
            self.inspect_pending = True                       # inspect once they're found (search below)
            # not watching them: wake/look like a call, inspect once they're found
        if self.mode == 'track':                              # already watching them: acknowledge with a tilt
            self.roll_tgt, self.roll_until = 500 + random.choice((-1, 1)) * TILT_PULSES, now + TILT_S; return
        if self.mode == 'sleep':
            if now < self.cool_until or not self.take_arm():
                return
            self.awake_since, self.next_temp = now, now + TEMP_EVERY
        self.set_mode('curious', 'heard yo buddy')
        # look where they were last seen, else where they usually turn up (learned), else straight into the room
        pose = st.get('arm_pose') or {}
        tgt = self.owner_pose or (lambda p: (int(p[0]), int(p[1])) if p else None)(
            reappear.predict(self.reappear, 1) or reappear.predict(self.reappear, -1)) or NEUTRAL
        # search: where they were last seen (or learned spot) first, then sweep the room
        learned = [tuple(int(v) * PBIN + PBIN // 2 for v in k.split(','))
                   for k, n in sorted(self.presence.items(), key=lambda kv: -kv[1]) if n >= 3][:4]
        explore = EXPLORE_PTS[:max(0, 4 - len(learned))]     # less exploring the more it knows
        pts = ([tuple(tgt)] if tgt != NEUTRAL else []) + learned + explore + [NEUTRAL]
        self.search = {'pts': pts, 'i': 0, 't': now}
        self.lost = None
        self.event('called_search', first=pts[0], learned_spots=len(learned))

    def _cell(self, pan, wr):
        return f'{int(pan) // CELL},{int(wr) // CELL}'

    def _dir_of(self, box, pan, wr, K):
        """Head direction (pan, wrist pulses) that centres a detection box (same sign rules as tracking)."""
        u, v = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        ex = math.atan((u - K[0, 2]) / K[0, 0]); ey = math.atan((v - K[1, 2]) / K[1, 1])
        return (max(PAN_LIM[0], min(PAN_LIM[1], int(pan - ex / RAD_PER_PULSE))),
                max(WRIST_LIM[0], min(WRIST_LIM[1], int(wr - ey / RAD_PER_PULSE))))

    def _interest(self, d, tgt, rgb_w):
        """How interesting a detection is: new here / changed / cut off at the frame edge / unknown thing."""
        label, score, box = d
        if label in ('a face', 'a person', 'a hand'):
            return 0.0
        cell = self.room_mem.get(self._cell(*tgt), {})
        seen = cell.get('labels', {}).get(label)
        novelty = 1.0 if seen is None else min(1.0, (time.time() - seen) / 86400.0)    # new, or not seen for a day
        edge = 0.3 if box[0] < 0.08 * rgb_w or box[2] > 0.92 * rgb_w else 0.0           # half out of view: look
        unknown = 0.4 if label == 'an object' else 0.0
        done = any(math.dist(tgt, l) < 40 for l in self.room_scan['looked'])          # already looked at it
        return 0.0 if done else score + novelty + edge + unknown

    def room_scan_step(self, now, st, rgb, dets, K):
        """Inquisitive look-around: go to the most interesting thing (centre it, dwell, keep a close look), else
        glance at the least recently seen part of the room; stop after SCAN_S or MAX_LOOKS close looks."""
        R, pose = self.room_scan, st.get('arm_pose') or {}
        pan, wr = pose.get('1', 500), pose.get('4', 178)
        K_msg = self.n.last.get('info')
        c = self._cell(pan, wr)                               # remember what is here, now
        cell = self.room_mem.setdefault(c, {'t': 0, 'labels': {}})
        cell['t'] = now
        for d in dets:
            if d[0] not in ('a face', 'a person', 'a hand'):
                cell['labels'][d[0]] = now
        if R['target'] is not None:                           # moving to / dwelling on a target
            tp, tw = R['target']['dir']
            if (abs(pan - tp) > 10 or abs(wr - tw) > 10) and now - R['target']['t'] < 3.0:
                self.go_to(pan, wr, tp, tw, gain=0.012, vmax=1.2); R['arrive'] = None; return   # saccade
            self.send(0.0, 0.0)
            if R['arrive'] is None:
                R['arrive'] = now; return
            if now - R['arrive'] < R['dwell']:
                return
            if R['target']['kind'] == 'look':                 # keep the close look for identifying
                fn = f"{len(R['shots']) + 1}.jpg"
                cv2.imwrite(os.path.join(R['dir'], fn), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                R['shots'].append({'view': len(R['shots']) + 1, 'pan': pan, 'wrist': wr, 'file': fn,
                                   'focus': R['target']['label'], 'new': R['target']['new'],
                                   'dets': [(d[0], round(float(d[1]), 3), [int(x) for x in d[2]]) for d in dets]})
            try:                                              # learn what this look was worth (never breaks looking)
                got = self.looks.record(c, pan, wr, self.n.last.get('depth'), K_msg, self.n.tf, self.n.last.get('scan'))
                if got: self.event('look_learned', **got)
            except Exception as ex:
                self.event('look_learn_error', err=str(ex)[:120])
            R['looked'].append(R['target']['dir']); R['target'] = None
        if now - R['t0'] > SCAN_S or len(R['shots']) >= MAX_LOOKS:
            if not R['shots']:                                # nothing caught its interest: keep the view it has
                cv2.imwrite(os.path.join(R['dir'], '1.jpg'), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
                R['shots'].append({'view': 1, 'pan': pan, 'wrist': wr, 'file': '1.jpg', 'focus': None, 'new': False,
                                   'dets': [(d[0], round(float(d[1]), 3), [int(x) for x in d[2]]) for d in dets]})
            return self.room_scan_finish(now)
        # what here is worth a closer look?
        best = None
        for d in dets:
            tgt = self._dir_of(d[2], pan, wr, K)
            v = self._interest(d, tgt, rgb.shape[1])
            if v > 0.6 and (best is None or v > best[0]):
                best = (v, d, tgt)
        if best:
            v, d, tgt = best
            new = d[0] not in self.room_mem.get(self._cell(*tgt), {}).get('labels', {})
            R['target'] = {'dir': tgt, 't': now, 'kind': 'look', 'label': d[0], 'new': new}
            R['dwell'] = random.uniform(1.2, 2.2) if new else random.uniform(0.6, 1.1)
            self.event('scan_interest', label=d[0], interest=round(v, 2), new=new)
            return
        # nothing grabs it: glance at the least recently seen part of the room (a little randomness, varied height)
        cands = [(p, w) for p in range(PAN_RANGE[0], PAN_RANGE[1] + 1, CELL) for w in WRIST_ROWS]
        cands = [x for x in cands if all(math.dist(x, l) > 60 for l in R['looked'])] or cands
        age = lambda x: now - self.room_mem.get(self._cell(*x), {}).get('t', 0)
        # least recently seen, weighed with what a look there has been worth (learned: shows more, costs the LiDAR less)
        tgt = max(random.sample(cands, min(6, len(cands))),
                  key=lambda x: age(x) - 0.002 * math.dist(x, (pan, wr)) + 600.0 * self.looks.value(self._cell(*x)))
        R['target'] = {'dir': tgt, 't': now, 'kind': 'glance', 'label': None, 'new': False}
        R['dwell'] = random.uniform(0.3, 0.7)

    def room_scan_finish(self, now):
        R = self.room_scan
        try:
            json.dump(self.room_mem, open(ROOM_MEM + '.tmp', 'w')); os.replace(ROOM_MEM + '.tmp', ROOM_MEM)
        except OSError:
            pass
        tiles = [cv2.resize(cv2.imread(os.path.join(R['dir'], sh['file'])), (320, 200)) for sh in R['shots']]
        for k, t in enumerate(tiles):
            cv2.putText(t, str(k + 1), (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        while len(tiles) < 6:
            tiles.append(np.zeros((200, 320, 3), np.uint8))
        cv2.imwrite(os.path.join(R['dir'], 'mosaic.jpg'), np.vstack([np.hstack(tiles[:3]), np.hstack(tiles[3:6])]))
        json.dump({'t': now, 'shots': R['shots'], 'secs': round(now - R['t0'], 1), 'looked': len(R['looked'])},
                  open(os.path.join(R['dir'], 'meta.json'), 'w'), indent=1)
        self.looks.save()
        self.event('room_scan_done', dir=R['dir'], close_looks=len(R['shots']), glances=len(R['looked']))
        self.room_scan = None
        self.set_mode('curious', 'looked around the room')

    def lost_step(self, now, st):
        """After losing the owner: follow-through -> pause -> look where they likely reappear (learned, else further
        toward where they left) -> settle to neutral -> wait, glancing there every 15-25 s. Sleep rules unchanged."""
        L, pose = self.lost, st.get('arm_pose') or {}
        pan, wr, t = pose.get('1', 500), pose.get('4', 178), now - self.lost['t0']
        if t < LOST_S + 0.6:                                  # (follow-through ran while still in track)
            self.send(0.0, 0.0); return
        if 'target' not in L:
            m = self.world.matt(max_age=600)                  # its world model: where he was within the last 10 min
            hp = W.head_pan_toward(self.n.tf, (m['x'], m['y'])) if m else None
            pr = reappear.predict(self.reappear, L['side'])
            if hp is not None:
                L['target'] = (int(max(PAN_LIM[0], min(PAN_LIM[1], hp[0]))), L['wrist']); L['learned'] = 'world'
                self.event('world_look', matt={k: round(m[k], 2) for k in ('x', 'y')}, age_s=round(now - m['t']), pan=L['target'][0])
            else:
                L['target'] = (int(pr[0]), int(pr[1])) if pr else (max(PAN_LIM[0], min(PAN_LIM[1], L['pan'] - L['side'] * 90)), L['wrist'])
                L['learned'] = pr is not None
            L['t_look'] = now
            self.event('lost_look', target=L['target'], learned=L['learned'])
        if now - L['t_look'] < 3.5:
            self.go_to(pan, wr, *L['target'], gain=0.006, vmax=0.8); return
        if now - L['t_look'] < 6.0:
            self.send(0.0, 0.0); return
        if now >= L.setdefault('next_glance', now + random.uniform(15, 25)):
            L['glance_until'], L['next_glance'] = now + 2.5, now + random.uniform(15, 25)
        if now < L.get('glance_until', 0.0):
            self.go_to(pan, wr, *L['target'], gain=0.006, vmax=0.7)
        else:
            self.go_to(pan, wr, *NEUTRAL, gain=0.003, vmax=0.35)      # slow drift home, then still

    def acquire(self, rgb, ap, dets, now):
        pose = (self.n.state() or {}).get('arm_pose') or {}
        gap = now - self.last_seen if self.last_seen else None
        self.event('found', pan=pose.get('1', 500), wrist=pose.get('4', 178), gap_s=round(gap, 1) if gap else None,
                   learned_look=bool(self.lost and self.lost.get('learned')), searched=self.search is not None)
        self.lost, self.search = None, None
        if self.inspect_pending:                              # called with "what do you see": now look at the hands
            self.inspect_pending, self.inspect_until = False, now + INSPECT_S
        if gap is None or gap > 5.0:                          # a real reunion, not a detection flicker: curious tilt
            self.roll_tgt, self.roll_until = 500 + random.choice((-1, 1)) * TILT_PULSES, now + TILT_S
        fn = time.strftime('%H%M%S', time.gmtime()) + f'_{ap[2]}.jpg'
        cv2.imwrite(os.path.join(self.dir if self.day else HERE, fn), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        self.event('acquire', what=ap[2], frame=fn, dets=[(d[0], round(d[1], 3), [int(x) for x in d[2]]) for d in dets])


def main():
    # own the SIGINT/SIGTERM: the context must stay alive so the finally-block can release the arm
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    mind = Mind()
    mind.event('start')
    mind.publish_mode()
    mind.n.spin_until(lambda: all(k in mind.n.last for k in ('rgb', 'info', 'state')), 60, 'topics')
    print('mind running', flush=True)
    try:
        while rclpy.ok():
            rclpy.spin_once(mind.n, timeout_sec=0.02)       # was 5 ms: a 200 Hz poll for a ~15 fps camera
            mind.step()
    except KeyboardInterrupt:
        pass
    finally:
        mind.release_arm()
        mind.event('stop')
        mind.n.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
