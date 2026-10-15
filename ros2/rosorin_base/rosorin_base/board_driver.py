"""Controller-board owner: telemetry (IMU, battery) + gated wheel motion. docs/motion_safety.md.

Single owner of the port (TIOCEXCL). Motion disabled at start; enable via ~/enable.
Stops: deadman timeout, latched e-stop (topic 'estop', ANY gamepad button, >1 cmd_vel publisher).
Board key1 handling is kept but untested (key not reachable on this robot).
"""
import collections
import fcntl
import json
import queue
import math
import os
import select
import struct
import statistics
import termios
import threading
import time

import rclpy
from rcl_interfaces.msg import SetParametersResult
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState
from rclpy.executors import ExternalShutdownException
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.task import Future
from control_msgs.action import FollowJointTrajectory
from trajectory_msgs.msg import JointTrajectory
from control_msgs.msg import JointJog
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import BatteryState, Imu, JointState as JointStateMsg, Joy, LaserScan
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

from rosorin_base.arm import RAD_PER_PULSE, Trajectory, WaypointTrajectory, joints_to_pulses, check_goal, pulses_to_joints, fill_optional, torque_all, move_frame as arm_move_frame, stop_frame as arm_stop_frame
from rosorin_base.gyro_bias import GyroBiasTracker
from rosorin_base import arm_model
from rosorin_base.motion import STOP_FRAME, MotionGate, integrate_pose, mecanum_rps, motor_frame
from rosorin_base.rrc_protocol import (FUNC_IMU, FUNC_SYS, FrameParser, decode_battery_mv, decode_imu,
                                       encode_frame)

G = 9.80665
FUNC_KEY = 6
FUNC_GAMEPAD = 8
FUNC_BUS_SERVO = 5
FUNC_BUZZER = 2
TONE_MODE = {'locked': 900, 'drive': 1800, 'arm': 3000}   # Hz, one 90 ms beep per mode change
TONE_ESTOP = (450, 700)              # long low tone: e-stop latched (also MODE pressed while stopped)
TONE_RESET = (3500, 60)              # short high beep: e-stop cleared from the pad
ARM_IDS = (1, 2, 3, 4, 5, 10)
# The claw (10) is not a joint of the arm: nothing the head, the drive pose or the wheels need depends on where it is.
# 2026-10-05: it stopped answering and every pose move (wake-up, drive pose -> wheel enable) refused for hours.
# A silent optional servo is carried at its last known pulse, reported in state (servo_silent) and logged.
ARM_OPTIONAL = (10,)
FOLLOW_TIMEOUT = 0.25                    # s without a Servo command -> stop following (hold)
JOG_TIMEOUT = 0.2                        # s without a JointJog -> decelerate to a stop
JOG_ACCEL = 6.0                          # rad/s^2: glide up/down, no steps (owner 2026-10-01: 3.0 -> 6.0, head too slow)
TUCK_TOL = 25               # pulses: joints 2-4 read-back vs owner's tuck (servo 2 capped at 960 vs 980)
SERVO2_STORED_MAX = 960     # servo 2 EEPROM angle limit (read 0x32: 80..960)
ARM_SETTLE_TOL = 3        # pulses; tolerance on move targets in slow single moves
PLUG_STEP_MV = 190         # mV one-step jump with wheels off = charger plugged/unplugged (log 2026-10-01: +249 / -226)
ARM_PICKUP_TOL = 10       # pulses; tolerance on the CURRENT pose at arm enable only (owner 2026-10-01; gravity sag)
# read-only servo queries: cmd -> (reply struct, name)
SERVO_READS = {0x05: ('<BBbh', 'pos'), 0x07: ('<BBbH', 'vin_mv'), 0x09: ('<BBbB', 'temp_c'),
               0x0D: ('<BBbb', 'torque')}
KEY_TRIP_EVENTS = {0x01, 0x02, 0x20}      # pressed, long-press, click


class BoardDriver(Node):
    def __init__(self):
        super().__init__('rosorin_board_driver')
        dp = self.declare_parameter
        self.dev = dp('device', '/dev/ttyACM0').value
        self.frame_id = dp('imu_frame_id', 'imu_link').value
        self.calib_s = dp('gyro_calib_seconds', 3.0).value
        self.calib_max_std = dp('gyro_calib_max_std_dps', 0.3).value
        self.estop_key = dp('estop_key', 1).value
        self.rate = dp('control_rate_hz', 50.0).value
        self.gate = MotionGate(max_lin=dp('max_linear', 0.2).value, max_ang=dp('max_angular', 1.0).value,
                               acc_lin=dp('max_accel_linear', 0.5).value,
                               acc_ang=dp('max_accel_angular', 2.0).value,
                               timeout=dp('cmd_timeout', 0.3).value,
                               sense_timeout=dp('scan_timeout', 0.5).value)   # LiDAR ring = ~0.1 s; 0 = gate off
        self.scan_blocked = False
        self.acc_off = list(dp('accel_offset_g', [0.0, 0.0, 0.0]).value)
        self.acc_scale = list(dp('accel_scale', [1.0, 1.0, 1.0]).value)
        self.gyro = GyroBiasTracker(tau=dp('gyro_bias_tau_s', 20.0).value,
                                    settle=dp('gyro_bias_settle_s', 1.0).value,
                                    max_dev=dp('gyro_bias_max_dev_dps', 0.5).value)
        # measured 2026-09-27 at rest: gyro std 0.0007-0.0011 rad/s
        self.gyro_var = dp('gyro_noise_rad_s', 0.0012).value ** 2
        # no wheel encoders (board protocol has no feedback): wheel/odom = gated COMMANDED velocity
        self.odom_frame = dp('odom_frame_id', 'odom').value
        self.base_frame = dp('base_frame_id', 'base_link').value
        self.odom_lin_var = dp('odom_linear_std', 0.05).value ** 2
        self.odom_ang_var = dp('odom_angular_std', 0.2).value ** 2
        # commanded -> actual body velocity (floor, LiDAR scan match, docs/odometry.md):
        # strafe 0.79/0.78 left, 0.73/0.76 right -> mean 0.765; forward ~1.02 (1 run, not applied)
        self.odom_scale_x = dp('odom_scale_x', 1.0).value
        self.odom_scale_y = dp('odom_scale_y', 0.765).value
        self.odom_pose = (0.0, 0.0, 0.0)
        self.odom_t = None
        self.calib, self.calib_t0 = [], None

        self.imu_pub = self.create_publisher(Imu, 'imu/data_raw', 10)
        self.bat_pub = self.create_publisher(BatteryState, 'battery', 10)
        self.state_pub = self.create_publisher(String, '~/state', 10)
        self.odom_pub = self.create_publisher(Odometry, 'wheel/odom', 10)
        self.joy_pub = self.create_publisher(Joy, 'gamepad', 10)
        self.create_subscription(Twist, 'cmd_vel', self.on_cmd, 10)
        # arrival of scans only (raw=True: no deserialization in the control thread)
        self.create_subscription(LaserScan, 'scan', lambda _: self.gate.sensed(time.monotonic()),
                                 qos_profile_sensor_data, raw=True)
        self.create_subscription(Bool, 'estop', self.on_estop, 10)
        self.create_service(SetBool, '~/enable', self.on_enable)
        self.create_service(Trigger, '~/reset_estop', self.on_reset)
        self.create_service(Trigger, '~/arm/read_state', self.on_arm_read)
        self.servo_q = queue.Queue()
        self.plugged = None                 # charger: True/False after a detected step, None = unknown
        try:                                # a driver restart doesn't move the cable: last detected state survives it
            self.plugged = json.load(open(os.path.expanduser('~/battery/plugged.json')))['plugged']
        except (OSError, ValueError, KeyError):
            pass
        self.arm_speed = dp('arm_max_speed', 50.0).value
        self.jog_speed = dp('arm_jog_max_speed', self.arm_speed).value   # JointJog (head tracking) cap, pulses/s
        self.arm_limits = {sid: tuple(dp(f'arm_limit_{sid}', [0, 0]).value) for sid in ARM_IDS}
        self.arm_rest = {sid: int(dp(f'arm_rest_{sid}', 500).value) for sid in ARM_IDS}
        self.arm_fold = {sid: int(dp(f'arm_fold_{sid}', self.arm_rest[sid]).value) for sid in ARM_IDS}   # step between rest and tuck
        self.arm_recover_speed = dp('arm_recover_speed', 20.0).value
        self.arm_untuck_speed = dp('arm_untuck_speed', 120.0).value      # recover starting from the tuck pose
        self.arm_tuck_speed = dp('arm_tuck_speed', 120.0).value          # tuck moves from a normal pose
        self.arm_tuck = {sid: int(dp(f'arm_tuck_{sid}', 500).value) for sid in ARM_IDS}
        self.arm_drive = {sid: int(dp(f'arm_drive_{sid}', 500).value) for sid in ARM_IDS}
        # camera pan for driving: -1 = leave the base joint where it is (owner; gamepad driving). Navigation sets
        # 500 (camera straight ahead) so the depth camera maps the floor the robot drives onto (PLAN M1).
        self.drive_pan = int(dp('drive_pan', -1).value)
        self.arm_enabled = False
        self.arm_pose = None            # last commanded/read pose {id: pulse}
        self.servo_silent = []          # arm servos that did not answer the last pose/state read
        # /joint_states source: commanded pulses while streaming, else servo read-back (any 0x05 read,
        # plus a slow refresh ONLY while wheels and arm are disabled: reads block the control loop)
        self.joint_pulses = {}
        self.joint_pub = self.create_publisher(JointStateMsg, 'joint_states', 10)
        # blocking arm moves (one servo command + sleep) stop this node's timers: no joint states until the move is
        # over, so the camera TF is frozen while the camera moves. arm_busy says so, before and after (latched);
        # depth_gate keeps those depth frames away from nvblox (2026-10-02).
        self.busy_pub = self.create_publisher(Bool, '~/arm_busy', QoSProfile(
            depth=1, reliability=QoSReliabilityPolicy.RELIABLE, durability=QoSDurabilityPolicy.TRANSIENT_LOCAL))
        self.busy_pub.publish(Bool(data=False))
        self.joint_refresh_s = dp('joint_refresh_s', 2.0).value
        self.joint_read_t = 0.0
        self.arm_traj = None
        self.arm_tick = 0
        # auto-tuck (owner OK 2026-09-29, 2 min): arm holding a pose (torque on) with no arm activity for auto_tuck_s,
        # wheels disabled, no e-stop -> the normal tuck. Unknown after a driver restart -> not holding until activity.
        self.auto_tuck_s = dp('auto_tuck_s', 120.0).value        # 0 = off
        # gamepad modes (owner 2026-09-29): MODE button cycles locked -> drive -> arm -> locked; every OTHER button is
        # still an e-stop. Sticks centred (15% dead zone) = no motion. Mapping measured 2026-09-29: left stick a3 (up +)
        # / a2 (right +); right stick a1 (up +) / a0 (right +); D-pad hat 15 up, 11 down, 9 left, 13 right, 0 none;
        # MODE = button bit 4.
        self.pad_mode_bit = dp('pad_mode_bit', 4).value
        self.pad_idle_s = dp('pad_idle_lock_s', 60.0).value
        self.pad_jog_speed = dp('pad_jog_speed', 80.0).value
        self.pad_sounds = dp('pad_sounds', False).value         # buzzer off until a safe tone is verified    # pulses/s at full stick
        self.pad = None                  # latest (buttons, hat, axes, t) written by the serial thread
        self.pad_mode_events = 0         # MODE presses not yet handled (serial thread -> control loop)
        self.pad_mode_down_t = -9.0
        self.pad_prev_mode = False
        self.pad_prev_hat = 0
        self.pad_mode = 'locked'
        self.pad_joint = 1
        self.pad_jog = {}
        self.pad_active_t = time.monotonic()
        self.pad_mode_hold_t = None
        self.pad_mode_off = 0
        self.pad_reset_req = False
        self.pad_hist = collections.deque(maxlen=20)
        self.arm_holding = False
        self.arm_active_t = time.monotonic()
        self.create_timer(1.0, self.auto_tuck_check)
        self.create_service(SetBool, '~/arm/enable', self.on_arm_enable)
        self.create_service(Trigger, '~/arm/recover', self.on_arm_recover)
        self.create_service(Trigger, '~/arm/relax', self.on_arm_relax)
        self.create_service(Trigger, '~/arm/tuck', self.on_arm_tuck)
        self.create_service(Trigger, '~/arm/drive', self.on_arm_drive)
        self.create_service(Trigger, '~/arm/pretuck', self.on_arm_pretuck)
        self.create_service(Trigger, '~/arm/stiffen', self.on_arm_stiffen)
        # MoveIt execution (2026-09-29): FollowJointTrajectory on arm_controller/follow_joint_trajectory. Same streaming
        # path and gates as arm/goal (arm enabled, no e-stop, limits) + the arm-model collision VETO on every waypoint
        # and segment midpoint (MoveIt plans; the model can still refuse). Too fast -> stretched, never sped up.
        self.fjt_model = arm_model.ArmModel(os.path.join(get_package_share_directory('rosorin_base'), 'config', 'robot_model.json'))
        self.fjt = None                                        # (goal_handle, future, trajectory) while executing
        ActionServer(self, FollowJointTrajectory, 'arm_controller/follow_joint_trajectory',
                     execute_callback=self.fjt_execute, goal_callback=lambda g: GoalResponse.ACCEPT,
                     cancel_callback=lambda g: CancelResponse.ACCEPT)
        self.add_on_set_parameters_callback(self.on_params)
        self.create_subscription(JointState, 'arm/goal', self.on_arm_goal, 10)
        # MoveIt Servo streaming input (2026-09-29): latest target from arm_controller/joint_trajectory, followed at
        # 25 Hz capped at arm_speed; limits + model veto per new target; no command for FOLLOW_TIMEOUT -> hold.
        self.follow = None                                     # {'target': {sid: pulse}, 't': monotonic}
        self.follow_vetoed = None
        self.create_subscription(JointTrajectory, 'arm_controller/joint_trajectory', self.on_follow, 10)
        # joint-velocity input (2026-09-29, for smooth tracking; Servo ran at ~1/6 speed, cause not found):
        # control_msgs/JointJog velocities (rad/s) on arm_controller/joint_jog -> integrated at 25 Hz with JOG_ACCEL,
        # speed cap arm_speed, limits + model veto per step (a blocked joint stops), JOG_TIMEOUT -> glide to a stop.
        self.jog = None                                        # {'cmd': {sid: rad/s}, 'vel': {sid: rad/s}, 'pos': {sid: float pulse}, 't'}
        self.create_subscription(JointJog, 'arm_controller/joint_jog', self.on_jog, 10)

        self.lock = threading.Lock()
        self.parser = FrameParser()
        self.fd = os.open(self.dev, os.O_RDWR | os.O_NOCTTY)
        fcntl.ioctl(self.fd, termios.TIOCEXCL)
        a = termios.tcgetattr(self.fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B1000000
        a[6][termios.VMIN] = 1
        a[6][termios.VTIME] = 0
        termios.tcsetattr(self.fd, termios.TCSANOW, a)
        self.write(STOP_FRAME)                       # known state at start
        self.last_rps, self.last_write = None, 0.0
        self.last_state = None

        self.running = True
        self.thread = threading.Thread(target=self.read_loop, daemon=True)
        self.thread.start()
        self.timer = self.create_timer(1.0 / self.rate, self.control)
        self.create_timer(0.1, self.publish_state)             # 10 Hz (was 2): clients see arm arrival quickly
        self.create_timer(0.1, self.publish_joints)
        self.get_logger().info(f'owning {self.dev}; motion DISABLED until ~/enable')

    # ---------- output
    def write(self, frame):
        with self.lock:
            os.write(self.fd, frame)

    def control(self):
        now = time.monotonic()
        if self.count_publishers('cmd_vel') > 1:
            self.trip('multiple cmd_vel publishers')
        if self.count_publishers('arm/goal') > 1:
            self.trip('multiple arm/goal publishers')
        self.arm_tick += 1
        self.pad_step(now)
        if self.arm_traj is not None and self.arm_tick % 2 == 0:       # 25 Hz streaming
            targets = self.arm_traj.at(now)
            self.write(arm_move_frame(targets, 40))
            self.arm_active_t = now
            self.arm_pose.update(targets)
            self.joint_pulses.update(targets)
            if self.arm_traj.done(now):
                if self.fjt and self.fjt[2] is self.arm_traj:
                    self.fjt_finish('ok')
                self.arm_traj = None
        elif self.follow is not None and self.arm_tick % 2 == 0:
            if now - self.follow['t'] > FOLLOW_TIMEOUT or not self.arm_enabled:
                self.joint_pulses.update({k: self.arm_pose[k] for k in self.follow['target']})
                self.follow = None                             # stale: servos hold the last streamed position
            else:
                # FRACTIONAL pulses internally + reported: Servo integrates from the reported state, and rounding it
                # stalled the loop (2026-09-29: 0.3 rad/s commanded -> ~0.04 rad/s). Only the servo frame is rounded.
                step = self.arm_speed * 2.0 / self.rate         # pulses per 25 Hz tick at the speed cap
                pos = self.follow.setdefault('pos', {k: float(self.arm_pose[k]) for k in self.follow['target']})
                for k, v in self.follow['target'].items():
                    pos[k] += max(-step, min(step, v - pos[k]))
                out = {k: int(round(v)) for k, v in pos.items()}
                if out != {k: self.arm_pose[k] for k in out}:
                    self.write(arm_move_frame(out, 40))
                    self.arm_pose.update(out)
                self.arm_activity(holding=True)
        elif self.jog is not None and self.arm_tick % 2 == 0:
            self.jog_step(now)
        if self.fjt and self.fjt[0].is_cancel_requested:
            self.arm_hold('trajectory canceled')
        vx, vy, wz = self.gate.step(now, 1.0 / self.rate)
        blocked = self.gate.block_reason(now) == 'no scan'
        if blocked != self.scan_blocked:                       # logged both ways: the stop and the recovery
            self.scan_blocked = blocked
            age = None if self.gate.sense_t is None else round(now - self.gate.sense_t, 2)
            if blocked:        # two call sites: rclpy refuses one call site that changes severity (it raised
                self.get_logger().warn(f'wheels held: no LiDAR scan for {age} s while commanded')   # and killed
            else:              # the driver on the first release - found on the stand, 2026-10-02)
                self.get_logger().info('LiDAR scan back: wheels released')
        self.publish_wheel_odom(now, vx, vy, wz)
        self.gyro.set_commanded_still(vx == 0.0 and vy == 0.0 and wz == 0.0, now)
        rps = mecanum_rps(vx, vy, wz)
        if rps != self.last_rps or now - self.last_write > 0.2:     # on change + 5 Hz refresh
            self.write(motor_frame(rps))
            self.last_rps, self.last_write = rps, now

    def trip(self, reason):
        first = self.gate.estop is None
        self.gate.trip(reason)
        self.write(STOP_FRAME)                       # immediate, not waiting for the timer
        self.last_rps = [0.0] * 4
        self.arm_hold('e-stop')
        self.pad_mode, self.pad_mode_events = 'locked', 0      # an e-stop always drops the gamepad to locked
        if first:
            self.tone(*TONE_ESTOP)
            self.get_logger().warn(f'E-STOP latched: {reason}')

    # ---------- gamepad modes (control loop only: mode changes wait on servo replies read by the serial thread)
    def tone(self, freq, ms):
        """Board buzzer (func 2, <HHHH freq, on ms, off ms, repeat> - factory SDK reference): one tone.
        2026-09-29: off-time 0 left the buzzer sounding continuously (stopped only by a raw 'buzzer off' frame), so
        tones now use a 100 ms off-time like the SDK examples, and sounds stay OFF unless `pad_sounds` is true."""
        if not self.pad_sounds:
            return
        self.write(encode_frame(FUNC_BUZZER, struct.pack('<HHHH', int(freq), int(ms), 100, 1)))

    def pad_set_mode(self, mode):
        old = self.pad_mode
        self.pad_mode = 'locked'
        if old == 'drive' and self.gate.enabled:
            self.gate.enable(False)
            self.write(STOP_FRAME)
        if old == 'arm' and self.arm_enabled:
            self.arm_hold('pad mode change')
        if mode == 'drive':                          # wheel enable moves the arm to the drive pose first
            r = self.on_enable(SetBool.Request(data=True), SetBool.Response())
            mode = mode if r.success else 'locked'
        elif mode == 'arm':
            if not self.arm_holding:                 # tucked/relaxed: untuck first (tuck is outside the limits)
                self.on_arm_recover(Trigger.Request(), Trigger.Response())
            r = self.on_arm_enable(SetBool.Request(data=True), SetBool.Response())
            if r.success:
                self.pad_jog = {k: float(v) for k, v in self.arm_pose.items()}
            else:
                mode = 'locked'
        self.pad_mode = mode
        self.pad_active_t = time.monotonic()
        self.tone(TONE_MODE[mode], 90)
        self.get_logger().info(f'pad mode {old} -> {mode}')

    def pad_step(self, now):
        """50 Hz: MODE presses, then the sticks for the current mode. Sticks centred (dead zone) = no motion."""
        if self.pad_reset_req:                              # MODE held 2 s while e-stopped (flag from serial thread)
            self.pad_reset_req = False
            if self.gate.estop is not None:
                was = self.gate.estop
                self.gate.reset()                           # motion stays disabled; gamepad comes back locked
                self.pad_mode, self.pad_mode_events = 'locked', 0
                self.tone(*TONE_RESET)
                self.get_logger().info(f'e-stop cleared from the gamepad (hold MODE 2 s; was: {was}); locked')
        if self.pad_mode_events:
            self.pad_mode_events = 0
            if self.gate.estop is not None:
                self.tone(*TONE_ESTOP)                      # stopped: MODE only reminds (hold 2 s to clear)
            else:
                self.pad_set_mode({'locked': 'drive', 'drive': 'arm', 'arm': 'locked'}[self.pad_mode])
        if self.pad_mode == 'locked' or self.pad is None:
            return
        _, hat, axes, _ = self.pad
        dz = lambda v: 0.0 if abs(v) < 0.15 else max(-1.0, min(1.0, v))
        # stick ROLES swapped (owner 2026-09-29): the 'left' variables below read the physical RIGHT stick (a0/a1),
        # the 'right' ones the physical LEFT stick (a2/a3). Drive: right stick = forward/strafe, left = turn.
        # Arm: right stick = joint jog, left stick = claw.
        lx, ly, rx, ry = dz(axes[0] / 127.0), dz(axes[1] / 127.0), dz(axes[2] / 127.0), dz(axes[3] / 127.0)
        if lx or ly or rx or ry or hat:
            self.pad_active_t = now
        elif now - self.pad_active_t > self.pad_idle_s:
            self.get_logger().info(f'pad: {self.pad_idle_s:.0f} s untouched -> locked')
            self.pad_set_mode('locked')
            return
        if self.pad_mode == 'drive':
            # left stick: forward/back + strafe (right = -y); right stick left/right: turn (right = -z). Centred: no
            # command at all -> the deadman stops the wheels within cmd_timeout.
            if self.gate.enabled and (lx or ly or rx):
                self.gate.set_cmd(ly * self.gate.max_lin, -lx * self.gate.max_lin, -rx * self.gate.max_ang, now,
                                  manual=True)
        elif self.pad_mode == 'arm' and self.arm_enabled and self.arm_traj is None:
            if hat in (9, 13) and hat != self.pad_prev_hat:                 # D-pad left/right: select joint 1-5
                self.pad_joint = (self.pad_joint - 2) % 5 + 1 if hat == 9 else self.pad_joint % 5 + 1
                self.tone(1800 + 250 * self.pad_joint, 35)   # short tick, pitch rises with the joint
            self.pad_prev_hat = hat
            if self.arm_tick % 2 == 0 and (ly or ry):                       # 25 Hz, like trajectory streaming
                tgt = {}
                for sid, v in ((self.pad_joint, ly), (10, ry)):           # left stick = joint, right stick = claw
                    if not v:
                        continue
                    lo, hi = self.arm_limits[sid]
                    self.pad_jog[sid] = min(hi, max(lo, self.pad_jog.get(sid, self.arm_pose[sid]) + v * self.pad_jog_speed * 0.04))
                    tgt[sid] = int(round(self.pad_jog[sid]))
                self.write(arm_move_frame(tgt, 40))
                self.arm_pose.update(tgt)
                self.joint_pulses.update(tgt)
                self.arm_active_t = now

    # ---------- inputs
    def on_cmd(self, m):
        self.gate.set_cmd(m.linear.x, m.linear.y, m.angular.z, time.monotonic())

    def on_estop(self, m):
        if m.data:
            self.trip('estop topic')

    def on_enable(self, req, res):
        # driving happens in the DRIVE pose (camera down on the floor ahead), never tucked (owner 2026-09-29):
        # enabling the wheels first moves the arm there (single slow move; arm must be disabled, no e-stop)
        # 2026-10-05: the wheels are NEVER enabled with the arm anywhere else. Before, an arm still held by the mind
        # (tracking the owner) made this step skip silently: a whole exploration run drove with the arm stretched to
        # one side, camera on the owner instead of the floor ahead (owner: "awkward ... could get snagged"). Now the
        # arm is taken over, moved, and checked; if it is not in the drive pose afterwards the enable is refused.
        if req.data and not self.gate.enabled and self.gate.estop is None:
            if self.arm_enabled or self.arm_traj is not None or self.follow is not None or self.jog is not None:
                self.arm_hold('wheels enabled')
            if not self.in_drive_pose():
                self.on_arm_drive(Trigger.Request(), Trigger.Response())
                if not self.in_drive_pose(tol=30):
                    res.success, res.message = False, 'refused: arm is not in the drive pose'
                    self.get_logger().warn(f'enable(True) -> {res.message}')
                    return res
        res.success = self.gate.enable(req.data)
        if not req.data:
            self.write(STOP_FRAME)
            self.wheels_off_t = time.monotonic()               # plug detection ignores the motor-stop transient
        res.message = 'enabled' if (req.data and res.success) else (
            'refused: e-stop latched (' + str(self.gate.estop) + ')' if req.data else 'disabled')
        self.get_logger().info(f'enable({req.data}) -> {res.message}')
        return res

    def on_reset(self, req, res):
        was = self.gate.estop
        self.gate.reset()
        res.success, res.message = True, f'e-stop cleared (was: {was}); motion still disabled'
        self.get_logger().info(res.message)
        return res

    def servo_query(self, sid, cmd, timeout=0.3):
        """Read-only query of one bus servo value; None on timeout/failure."""
        fmt, _ = SERVO_READS[cmd]
        while not self.servo_q.empty():
            self.servo_q.get_nowait()
        self.write(encode_frame(FUNC_BUS_SERVO, bytes([cmd, sid])))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                p = self.servo_q.get(timeout=max(0.0, end - time.monotonic()))
            except queue.Empty:
                break
            if len(p) == struct.calcsize(fmt) and p[0] == sid and p[1] == cmd:
                rid, rcmd, ok, val = struct.unpack(fmt, p)
                if ok == 0 and cmd == 0x05:
                    self.joint_pulses[sid] = val
                return val if ok == 0 else None
        return None

    def note_silent(self, silent):
        if silent != self.servo_silent:
            if silent:
                self.get_logger().error(f'arm servos not answering: {silent}'
                                        + (' (optional: arm and wheels carry on)' if set(silent) <= set(ARM_OPTIONAL) else ''))
            else:
                self.get_logger().info(f'arm servos all answering again (were silent: {self.servo_silent})')
            self.servo_silent = list(silent)

    def read_pose(self):
        """Pose of all arm servos {id: pulse}. A silent ARM_OPTIONAL servo is carried at its last known pulse (last
        read or command, else its rest value); a silent joint servo stays None and the callers refuse."""
        raw = {sid: self.servo_query(sid, 0x05) for sid in ARM_IDS}
        last = dict(self.arm_rest); last.update(self.arm_pose or {}); last.update(self.joint_pulses)
        pose, silent = fill_optional(raw, last, ARM_OPTIONAL)
        self.note_silent(silent)
        return pose

    def on_arm_read(self, req, res):
        if self.gate.enabled:
            res.success, res.message = False, 'refused: wheel motion enabled (reads would stall the deadman)'
            return res
        state = {}
        for sid in ARM_IDS:
            state[sid] = {name: self.servo_query(sid, cmd) for cmd, (_, name) in SERVO_READS.items()}
        silent = [sid for sid, v in state.items() if v['pos'] is None]
        self.note_silent(silent)
        res.success = set(silent) <= set(ARM_OPTIONAL)         # the message carries every servo as read
        res.message = json.dumps(state)
        self.get_logger().info(f'arm read: {res.message}')
        return res

    def on_params(self, params):
        """Arm limits / speed may change at runtime, only while the arm is disabled."""
        new_limits, new_speed = {}, None
        for p in params:
            if p.name.startswith('arm_limit_') or p.name == 'arm_max_speed':
                if self.arm_enabled:
                    return SetParametersResult(successful=False, reason='disable the arm first')
            if p.name.startswith('arm_limit_'):
                sid = int(p.name.rsplit('_', 1)[1])
                v = list(p.value)
                if sid not in ARM_IDS or len(v) != 2 or not 0 <= v[0] < v[1] <= 1000:
                    return SetParametersResult(successful=False, reason=f'invalid {p.name}: {v}')
                new_limits[sid] = (int(v[0]), int(v[1]))
            elif p.name == 'arm_max_speed':
                if not 1.0 <= float(p.value) <= 300.0:           # ceiling 100 -> 150 -> 300 (owner, 2026-09-29)
                    return SetParametersResult(successful=False, reason='arm_max_speed must be 1..300')
                new_speed = float(p.value)
            elif p.name == 'drive_pan':
                lo, hi = self.arm_limits.get(1, (0, 1000))
                if int(p.value) != -1 and not lo <= int(p.value) <= hi:
                    return SetParametersResult(successful=False, reason=f'drive_pan must be -1 or {lo}..{hi}')
                self.drive_pan = int(p.value)
                self.get_logger().info(f'drive_pan {self.drive_pan}')
        self.arm_limits.update(new_limits)
        if new_speed is not None:
            self.arm_speed = new_speed
        if new_limits or new_speed is not None:
            self.get_logger().info(f'arm params: limits {new_limits} speed {new_speed}')
        return SetParametersResult(successful=True)

    def arm_hold(self, why):
        # always on e-stop: single-command moves (recover/tuck/drive) run while arm_enabled is False
        if self.arm_traj is not None or self.arm_enabled or why == 'e-stop':
            self.write(arm_stop_frame(ARM_IDS))
            self.get_logger().info(f'arm hold ({why})')
        self.arm_traj = None
        self.arm_enabled = False
        self.follow = None
        self.jog = None
        if self.fjt:
            self.fjt_finish('canceled' if why == 'trajectory canceled' else f'stopped: {why}')

    def on_arm_enable(self, req, res):
        if not req.data:
            self.arm_hold('disable')
            res.success, res.message = True, 'arm disabled (holding)'
            return res
        if self.gate.estop is not None:
            res.success, res.message = False, f'refused: e-stop latched ({self.gate.estop})'
            return res
        if self.gate.enabled:
            res.success, res.message = False, 'refused: disable wheel motion first (pose read needed)'
            return res
        pose = self.read_pose()
        if any(v is None for v in pose.values()):
            res.success, res.message = False, f'refused: pose read failed {pose}'
            return res
        # current pose may sit a few pulses outside a limit after settling at it (e.g. 39 vs 40; 2026-10-01 servo 3
        # rests at 36 when sent to 45). Pickup only: every later move stays inside the owner's limits.
        tol = {sid: (lo - ARM_PICKUP_TOL, hi + ARM_PICKUP_TOL) for sid, (lo, hi) in self.arm_limits.items()}
        outside = check_goal(pose, tol)
        if outside:
            res.success, res.message = False, f'refused: current pose outside limits (+/-{ARM_PICKUP_TOL}): {outside}'
            return res
        self.arm_pose, self.arm_enabled = pose, True
        res.success, res.message = True, f'arm enabled at {pose}'
        self.arm_activity(holding=True)
        self.get_logger().info(res.message)
        return res

    def on_arm_recover(self, req, res):
        """Bring all joints to the rest pose with ONE slow move command (servo interpolates from its
        actual position, even if that is outside 0..1000 after a power-off sag). Does not enable."""
        if self.arm_enabled or self.gate.enabled or self.gate.estop is not None:
            res.success, res.message = False, 'refused: arm and wheels must be disabled, no e-stop'
            return res
        pose = self.read_pose()
        if any(v is None for v in pose.values()):
            res.success, res.message = False, f'refused: pose read failed {pose}'
            return res
        bad = check_goal(self.arm_rest, self.arm_limits)
        if bad:
            res.success, res.message = False, f'refused: rest pose outside limits: {bad}'
            return res
        # from the known tuck (same test as the tuck self-check) = normal untuck: faster (owner 2026-09-29), and by
        # way of the fold pose (the path run daily). Anything else (e.g. sagged after power-off, possibly outside
        # 0..1000) goes straight to rest at the slow, careful speed.
        from_tuck = all(abs(pose[j] - self.arm_tuck[j]) <= TUCK_TOL for j in (2, 3, 4))
        steps = [self.arm_fold, self.arm_rest] if from_tuck and self.arm_fold != self.arm_rest else [self.arm_rest]
        self.busy_pub.publish(Bool(data=True))
        cur, dur = pose, 0.0
        for target in steps:
            dist = max(abs(target[s] - cur[s]) for s in ARM_IDS)
            d = max(1.0, dist / self.arm_untuck_speed) if from_tuck else max(3.0, dist / self.arm_recover_speed)
            self.get_logger().info(f'arm recover {cur} -> {target} in {d:.1f} s (single command'
                                   f'{", untuck from tuck" if from_tuck else ""})')
            self.write(arm_move_frame(target, int(d * 1000)))
            time.sleep(d + 0.5)
            cur, dur = target, dur + d
        after = self.read_pose()
        self.busy_pub.publish(Bool(data=False))
        outside = check_goal({k: v for k, v in after.items() if v is not None}, self.arm_limits)
        res.success = outside is None and None not in after.values()
        res.message = json.dumps({'before': pose, 'after': after, 'duration_s': round(dur, 1),
                                  'inside_limits': outside is None})
        self.get_logger().info(f'arm recover result: {res.message}')
        self.arm_activity(holding=True)
        return res

    def on_arm_relax(self, req, res):
        """Torque OFF on all arm servos (func 5, 0x0C): the arm settles under gravity. Parking/test only.
        Wake-up is a separate step (read settled pose, command it) - not done here."""
        if self.arm_enabled or self.gate.enabled:
            res.success, res.message = False, 'refused: arm and wheels must be disabled'
            return res
        before = self.read_pose()
        self.get_logger().warn(f'arm RELAX (torque off) from {before}')
        self.busy_pub.publish(Bool(data=True))
        torque = {}
        for attempt in range(3):
            for sid in ARM_IDS:
                if torque.get(sid) == 0:
                    continue
                self.write(encode_frame(FUNC_BUS_SERVO, bytes([0x0C, sid])))
                time.sleep(0.05)
            torque = {sid: self.servo_query(sid, 0x0D) for sid in ARM_IDS}
            if torque_all(torque, 0, ARM_OPTIONAL):
                break
        time.sleep(2.5)
        after = self.read_pose()
        self.busy_pub.publish(Bool(data=False))
        res.success = torque_all(torque, 0, ARM_OPTIONAL)
        res.message = json.dumps({'before': before, 'after': after, 'torque_state': torque, 'attempts': attempt + 1})
        self.get_logger().info(f'arm relaxed: {res.message}')
        self.arm_activity(holding=False)
        return res

    def slow_single_move(self, target, why):
        """One move command, duration from the largest distance at arm_recover_speed (>= 3 s). Works from
        any current pose (incl. outside limits / torque off). Target must be inside limits (+/- tol)."""
        tol = {sid: (lo - ARM_SETTLE_TOL, hi + ARM_SETTLE_TOL) for sid, (lo, hi) in self.arm_limits.items()}
        bad = check_goal(target, tol)
        if bad:
            return None, f'target outside limits: {bad}'
        pose = self.read_pose()
        if any(v is None for v in pose.values()):
            return None, f'pose read failed {pose}'
        dist = max(abs(target[s] - pose[s]) for s in ARM_IDS)
        # from the known tuck or a normal pose inside limits: fast (owner 2026-09-29); anything else stays slow
        from_tuck = all(abs(pose[j] - self.arm_tuck[j]) <= TUCK_TOL for j in (2, 3, 4))
        normal = from_tuck or check_goal(pose, tol) is None
        dur = max(1.0, dist / self.arm_tuck_speed) if normal else max(3.0, dist / self.arm_recover_speed)
        self.get_logger().info(f'arm {why}: {pose} -> {target} in {dur:.1f} s (single command{", fast" if normal else ""})')
        self.busy_pub.publish(Bool(data=True))
        self.write(arm_move_frame(target, int(dur * 1000)))
        time.sleep(dur + 0.5)
        after = self.read_pose()
        self.busy_pub.publish(Bool(data=False))
        return {'before': pose, 'after': after, 'duration_s': round(dur, 1)}, None

    def _named_ok(self, res):
        if self.arm_enabled or self.gate.enabled or self.gate.estop is not None:
            res.success, res.message = False, 'refused: arm and wheels must be disabled, no e-stop'
            return False
        return True

    def on_arm_drive(self, req, res):
        """Any pose (incl. tuck, torque off) -> drive pose, one slow command; torque back on (holding)."""
        if not self._named_ok(res):
            return res
        target = dict(self.arm_drive)
        cur1 = self.servo_query(1, 0x05)
        if self.drive_pan >= 0:
            target[1] = self.drive_pan                        # navigation: camera straight ahead
        elif cur1 is not None:
            target[1] = cur1                                  # base does not need to move (owner)
        out, err = self.slow_single_move(target, 'to DRIVE')
        self.arm_activity(holding=True)
        res.success, res.message = out is not None, json.dumps(out) if out else f'refused: {err}'
        return res

    def pretuck_pose(self):
        return {sid: min(max(v, self.arm_limits[sid][0]), self.arm_limits[sid][1]) for sid, v in self.arm_tuck.items()}

    def on_arm_pretuck(self, req, res):
        """Move to pre-tuck only (servos stay on)."""
        if not self._named_ok(res):
            return res
        out, err = self.slow_single_move(self.pretuck_pose(), 'to PRE-TUCK')
        self.arm_activity(holding=True)
        res.success, res.message = out is not None, json.dumps(out) if out else f'refused: {err}'
        return res

    def on_arm_stiffen(self, req, res):
        """Torque ON in place: command the CURRENT pose (1 s), so nothing moves to an old target."""
        if not self._named_ok(res):
            return res
        pose = self.read_pose()
        if any(v is None or v < 0 or v > 1000 for v in pose.values()):
            res.success, res.message = False, f'refused: pose not commandable (read failed or outside 0..1000): {pose}'
            return res
        self.busy_pub.publish(Bool(data=True))
        self.write(arm_move_frame(pose, 1000))
        time.sleep(1.3)
        self.busy_pub.publish(Bool(data=False))
        torque = {sid: self.servo_query(sid, 0x0D) for sid in ARM_IDS}
        after = self.read_pose()
        res.success = torque_all(torque, 1, ARM_OPTIONAL)
        res.message = json.dumps({'commanded': pose, 'after': after, 'torque_state': torque})
        self.get_logger().info(f'arm stiffen: {res.message}')
        self.arm_activity(holding=True)
        return res

    def on_arm_tuck(self, req, res):
        """TUCK (owner-demonstrated 2026-09-27): slow move to DEFAULT, then slow move to the tuck approach
        (owner's tuck with joint 2 capped at the servo's stored max 960, claw closed), then torque off 1-5.
        The pose rests on itself (upper arm motor on the joint-2 servo): releasing changes nothing.
        Self-check from servo read-back vs the owner's tuck (no camera)."""
        if not self._named_ok(res):
            return res
        start = self.read_pose()
        default = dict(self.arm_fold)                        # fold (S-coil) first, then back onto the rest
        if start.get(1) is not None:
            default[1] = start[1]                             # base does not need to move (owner)
        default[10] = self.arm_limits[10][1]                 # claw closed
        approach = dict(self.arm_tuck)
        approach[2] = min(approach[2], SERVO2_STORED_MAX)    # servo 2 refuses to drive past its stored limit
        approach[10] = self.arm_limits[10][1]
        approach[1] = default[1]
        for target, why in ((default, 'tuck 1/2: to FOLD + claw closed'), (approach, 'tuck 2/2: to TUCK approach')):
            tol = {sid: (lo - ARM_SETTLE_TOL, hi + ARM_SETTLE_TOL) for sid, (lo, hi) in self.arm_limits.items()}
            tol[2] = (tol[2][0], SERVO2_STORED_MAX)
            bad = check_goal(target, tol)
            if bad:
                res.success, res.message = False, f'refused: {why}: {bad}'
                return res
            pose = self.read_pose()
            dist = max(abs(target[k] - pose[k]) for k in ARM_IDS if pose[k] is not None)
            # normal start (every joint read and inside limits +/- tol): faster tuck (owner 2026-09-29); else slow
            normal = None not in pose.values() and check_goal(pose, tol) is None
            dur = max(1.0, dist / self.arm_tuck_speed) if normal else max(3.0, dist / self.arm_recover_speed)
            self.get_logger().info(f'arm {why}: {pose} -> {target} in {dur:.1f} s')
            self.busy_pub.publish(Bool(data=True))
            self.write(arm_move_frame(target, int(dur * 1000)))
            time.sleep(dur + 0.5)
            self.busy_pub.publish(Bool(data=False))
        relax = self.on_arm_relax(req, Trigger.Response())
        time.sleep(1.0)
        settled = self.read_pose()
        torque = {sid: self.servo_query(sid, 0x0D) for sid in ARM_IDS}
        tucked = all(settled.get(j) is not None and abs(settled[j] - self.arm_tuck[j]) <= TUCK_TOL for j in (2, 3, 4))
        res.success = tucked and all(torque[j] == 0 for j in (1, 2, 3, 4, 5))
        res.message = json.dumps({'tucked': tucked, 'settled': settled, 'tuck_ref': self.arm_tuck,
                                  'torque': torque, 'start': start})
        self.get_logger().info(f'arm TUCK result: {res.message}')
        self.arm_activity(holding=not res.success)       # failed tuck: still holding, retried after auto_tuck_s
        return res

    def on_arm_goal(self, m):
        if not self.arm_enabled or self.gate.estop is not None:
            self.get_logger().warn('arm goal ignored: arm not enabled or e-stop latched')
            return
        try:
            goal = {int(n): int(round(p)) for n, p in zip(m.name, m.position)}
        except ValueError:
            self.get_logger().warn(f'arm goal ignored: names must be servo ids, got {list(m.name)}')
            return
        why = check_goal(goal, self.arm_limits)
        if why:
            self.get_logger().warn(f'arm goal refused: {why}')
            return
        start = {sid: self.arm_pose[sid] for sid in goal}
        if self.fjt:
            self.fjt_finish('preempted by arm/goal')
        self.arm_traj = Trajectory(start, goal, self.arm_speed, time.monotonic())
        self.arm_activity(holding=True)
        self.get_logger().info(f'arm move {start} -> {goal} in {self.arm_traj.T:.2f} s')

    def on_jog(self, m):
        if not self.arm_enabled or self.gate.estop is not None or self.fjt is not None or self.arm_traj is not None:
            return
        try:
            cmd = {int(n[5:]): float(v) for n, v in zip(m.joint_names, m.velocities) if n.startswith('joint')}
        except ValueError:
            return
        if self.jog is None:
            self.jog = {'cmd': {}, 'vel': {}, 'pos': {k: float(self.arm_pose[k]) for k in (1, 2, 3, 4, 5)}}
        self.jog['cmd'].update(cmd)
        self.jog['t'] = time.monotonic()

    def jog_step(self, now):
        j, dt = self.jog, 2.0 / self.rate
        if not self.arm_enabled:
            self.jog = None
            return
        stale = now - j['t'] > JOG_TIMEOUT
        vmax = self.jog_speed * RAD_PER_PULSE
        moving = False
        for k in set(j['cmd']) | set(j['vel']):
            want = 0.0 if stale else max(-vmax, min(vmax, j['cmd'].get(k, 0.0)))
            v = j['vel'].get(k, 0.0)
            v += max(-JOG_ACCEL * dt, min(JOG_ACCEL * dt, want - v))
            # +rad = -pulse (flipped servos)
            nxt = j['pos'][k] - v * dt / RAD_PER_PULSE
            lo, hi = self.arm_limits[k]
            if not lo <= nxt <= hi:
                nxt, v = min(max(nxt, lo), hi), 0.0
            j['vel'][k], j['pos'][k] = v, nxt
            moving = moving or abs(v) > 1e-4
        out = {k: int(round(p)) for k, p in j['pos'].items()}
        # efficiency (owner 2026-10-01): the mind streams zero-velocity jogs while holding still; the veto (~9 ms)
        # and a joint_states publish ran 25x/s for an unchanged pose (board_driver main thread 60 % CPU). The veto
        # guards what is written to the servos, so it runs exactly when the commanded pose changes.
        if out != {k: self.arm_pose[k] for k in out}:
            full = dict(self.arm_pose); full.update(out)
            hits = [h for h in arm_model.collisions(self.fjt_model, full) if set(h) != {'link5', 'camera_link0'}]
            if hits:                                           # would collide: stop here, keep the last safe pose
                self.get_logger().warn(f'jog stopped by arm model veto: {hits}', throttle_duration_sec=2.0)
                j['pos'] = {k: float(self.arm_pose[k]) for k in j['pos']}
                j['vel'] = {k: 0.0 for k in j['vel']}
                return
            self.write(arm_move_frame(out, 40))
            self.arm_pose.update(out)
        self.joint_pulses.update(j['pos'])
        self.arm_activity(holding=True)
        if moving:
            self.publish_joints()                              # otherwise the 10 Hz timer reports the held pose
        if stale and not moving:
            self.jog = None

    def on_follow(self, m):
        if not self.arm_enabled or self.gate.estop is not None or self.fjt is not None or self.arm_traj is not None:
            return
        if not m.points:
            return
        try:
            tgt = joints_to_pulses(m.joint_names, m.points[0].positions)
        except ValueError as e:
            self.get_logger().warn(f'follow ignored: {e}', throttle_duration_sec=5.0)
            return
        why = check_goal(tgt, self.arm_limits)
        if why:
            self.get_logger().warn(f'follow target refused: {why}', throttle_duration_sec=2.0)
            return
        full = dict(self.arm_pose); full.update(tgt)
        hits = [h for h in arm_model.collisions(self.fjt_model, full) if set(h) != {'link5', 'camera_link0'}]
        if hits:
            if self.follow_vetoed != hits:
                self.get_logger().warn(f'follow target vetoed by arm model: {hits}')
            self.follow_vetoed = hits
            return
        self.follow_vetoed = None
        ftgt = {int(nm[5:]): 500.0 - rad / RAD_PER_PULSE for nm, rad in zip(m.joint_names, m.points[0].positions)}
        if self.follow is None:
            self.follow = {'target': ftgt, 't': time.monotonic()}
        else:
            self.follow['target'], self.follow['t'] = ftgt, time.monotonic()
        # report the COMMAND at once (ideal-controller feedback): Servo integrates from joint_states, and even one or
        # two cycles of lag cut its speed to ~10-20 % (2026-09-29). The servos follow at <= arm_speed behind it.
        self.joint_pulses.update(ftgt)
        self.publish_joints()

    def fjt_finish(self, outcome):
        gh, fut, _ = self.fjt
        self.fjt = None
        if not fut.done():
            fut.set_result(outcome)

    async def fjt_execute(self, gh):
        res = FollowJointTrajectory.Result()

        def fail(code, msg):
            self.get_logger().warn(f'trajectory refused: {msg}')
            res.error_code, res.error_string = code, msg
            gh.abort()
            return res

        tr = gh.request.trajectory
        if not self.arm_enabled or self.gate.estop is not None:
            return fail(res.INVALID_GOAL, 'arm not enabled or e-stop latched')
        if self.fjt is not None or self.arm_traj is not None:
            return fail(res.INVALID_GOAL, 'busy: another arm move is running')
        try:
            pts = [(p.time_from_start.sec + p.time_from_start.nanosec * 1e-9, joints_to_pulses(tr.joint_names, p.positions))
                   for p in tr.points]
        except ValueError as e:
            return fail(res.INVALID_JOINTS, str(e))
        if not pts:
            return fail(res.INVALID_GOAL, 'empty trajectory')
        start = {k: self.arm_pose[k] for k in pts[0][1]}
        if pts[0][0] <= 0.0:                                   # MoveIt's first point = its view of the current state
            off = max(abs(pts[0][1][k] - start[k]) for k in start)
            if off > 15:
                return fail(res.INVALID_GOAL, f'start {off} pulses from the actual pose (max 15)')
            pts = pts[1:]
        for t, p in pts:
            why = check_goal(p, self.arm_limits)
            if why:
                return fail(res.INVALID_GOAL, why)
        if any(tb <= ta for (ta, _), (tb, _) in zip([(0.0, None)] + pts, pts)):
            return fail(res.INVALID_GOAL, 'time_from_start not strictly increasing')
        w = WaypointTrajectory(start, pts, self.arm_speed, time.monotonic())
        for smp in w.samples():
            full = dict(self.arm_pose); full.update(smp)
            hits = [h for h in arm_model.collisions(self.fjt_model, full) if set(h) != {'link5', 'camera_link0'}]
            if hits:                                           # (link5, camera_link0) = known model false positive
                return fail(res.PATH_TOLERANCE_VIOLATED, f'arm model veto: {hits} at {smp}')
        fut = Future()
        w.t0 = time.monotonic()
        self.fjt = (gh, fut, w)
        self.arm_traj = w
        self.arm_activity(holding=True)
        self.get_logger().info(f'trajectory: {len(pts)} points, {w.T:.2f} s (stretch x{w.scale:.2f}) -> {w.goal}')
        outcome = await fut
        if outcome == 'ok':
            gh.succeed(); res.error_code = res.SUCCESSFUL
        elif outcome == 'canceled':
            gh.canceled(); res.error_code, res.error_string = res.SUCCESSFUL, 'canceled'
        else:
            gh.abort(); res.error_code, res.error_string = res.PATH_TOLERANCE_VIOLATED, outcome
        self.get_logger().info(f'trajectory {outcome}')
        return res

    def arm_activity(self, holding):
        """Any arm command/move resets the auto-tuck idle clock; holding = torque on in a pose (not tucked/relaxed)."""
        self.arm_active_t = time.monotonic()
        self.arm_holding = holding

    def in_drive_pose(self, tol=10):
        """Arm read back within tol pulses of the drive pose on joints 2-5 (base joint 1 may be anywhere)."""
        want = dict(self.arm_drive)
        if self.drive_pan >= 0:
            want[1] = self.drive_pan
        pose = {sid: self.servo_query(sid, 0x05) for sid in ((1, 2, 3, 4, 5) if self.drive_pan >= 0 else (2, 3, 4, 5))}
        return all(v is not None and abs(v - want[s]) <= tol for s, v in pose.items())

    def auto_tuck_check(self):
        """1 Hz: tuck an arm left holding with no activity for auto_tuck_s (wheels disabled, no e-stop, not moving)."""
        if self.gate.enabled:
            self.arm_active_t = time.monotonic()               # driving (arm in drive pose) counts as activity
        if self.auto_tuck_s <= 0 or not self.arm_holding or self.arm_traj is not None:
            return
        if self.gate.enabled or self.gate.estop is not None:
            return
        if time.monotonic() - self.arm_active_t < self.auto_tuck_s:
            return
        self.get_logger().info(f'AUTO-TUCK: arm idle {self.auto_tuck_s:.0f} s while holding')
        if self.arm_enabled:
            self.arm_hold('auto-tuck')                        # tuck requires the arm disabled (holding)
        res = self.on_arm_tuck(Trigger.Request(), Trigger.Response())
        if not res.success:
            self.get_logger().warn(f'AUTO-TUCK failed, retry after {self.auto_tuck_s:.0f} s: {res.message[:200]}')

    def publish_joints(self):
        now = time.monotonic()
        idle = not self.gate.enabled and not self.arm_enabled and self.arm_traj is None
        if idle and now - self.joint_read_t > self.joint_refresh_s:
            self.joint_read_t = now
            for sid in (1, 2, 3, 4, 5):
                self.servo_query(sid, 0x05, timeout=0.1)
        js = pulses_to_joints(self.joint_pulses)
        if js is None:
            return
        m = JointStateMsg()
        m.header.stamp = self.get_clock().now().to_msg()
        m.name, m.position = js
        self.joint_pub.publish(m)

    def publish_state(self):
        now = time.monotonic()
        s = json.dumps({'enabled': self.gate.enabled, 'estop': self.gate.estop, 'plugged': self.plugged,
                        'arm_enabled': self.arm_enabled, 'arm_moving': self.arm_traj is not None or self.follow is not None or self.jog is not None,
                        'arm_pose': self.arm_pose,
                        'servo_silent': self.servo_silent,
                        'blocked': self.gate.block_reason(now), 'body_vel': [round(v, 3) for v in self.gate.cur],
                        'scan_age': None if self.gate.sense_t is None else round(now - self.gate.sense_t, 2),
                        'gyro_bias_dps': [round(b, 4) for b in self.gyro.bias],
                        'gyro_bias_tracking': self.gyro.tracking(now),
                        'arm_holding': self.arm_holding, 'pad_mode': self.pad_mode, 'pad_joint': self.pad_joint,
                        'auto_tuck_in_s': (round(max(0.0, self.auto_tuck_s - (now - self.arm_active_t)), 1)
                                           if self.arm_holding and self.auto_tuck_s > 0 else None)})
        self.state_pub.publish(String(data=s))

    # ---------- telemetry
    def read_loop(self):
        while self.running:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if not ready:
                continue
            data = os.read(self.fd, 512)
            try:
                for func, payload in self.parser.feed(data):
                    if func == FUNC_IMU and len(payload) == 24:
                        self.publish_imu(payload)
                    elif func == FUNC_SYS:
                        mv = decode_battery_mv(payload)
                        if mv is not None:
                            self.publish_battery(mv)
                    elif func == FUNC_BUS_SERVO:
                        self.servo_q.put(payload)
                    elif func == FUNC_GAMEPAD and len(payload) == 7:
                        buttons, hat, *axes = struct.unpack('<HB4b', payload)
                        j = Joy()
                        j.header.stamp = self.get_clock().now().to_msg()
                        j.axes = [a / 127.0 for a in axes] + [float(hat)]      # raw order; hat raw value
                        j.buttons = [(buttons >> i) & 1 for i in range(16)]
                        self.joy_pub.publish(j)
                        mode_mask = 1 << self.pad_mode_bit
                        self.pad_hist.append((round(time.monotonic(), 2), payload.hex()))   # raw frames, for diagnosis
                        # any button except MODE trips on its FIRST frame. A 2-frame confirmation was tried 2026-09-29
                        # and REVERTED: real quick taps last one frame at the pad's 20 Hz (8 owner taps -> 0 trips).
                        other = buttons & ~mode_mask
                        if other and self.gate.estop is None:
                            self.trip('gamepad button 0x%04x' % other)
                            self.get_logger().warn('gamepad trip raw frames (t, hex <HB4b>): %s' % list(self.pad_hist)[-6:])
                        now = time.monotonic()
                        pressed = bool(buttons & mode_mask)
                        if pressed and not self.pad_prev_mode and now - self.pad_mode_down_t > 0.6:
                            self.pad_mode_down_t = now                  # rising edge, 0.6 s lockout (pad pulses)
                            self.pad_mode_events += 1
                        self.pad_prev_mode = pressed
                        if pressed:
                            self.pad_mode_off = 0
                            if self.pad_mode_hold_t is None:
                                self.pad_mode_hold_t = now
                            elif now - self.pad_mode_hold_t >= 2.0 and self.gate.estop is not None:
                                self.pad_reset_req = True
                                self.pad_mode_hold_t = now + 999    # one reset per hold
                        else:
                            self.pad_mode_off += 1
                            if self.pad_mode_off >= 3:
                                self.pad_mode_hold_t = None
                        self.pad = (buttons, hat, axes, now)            # acted on by the control loop
                    elif func == FUNC_KEY:
                        self.get_logger().info(f'key frame: {payload.hex()}')
                        if len(payload) >= 2 and payload[0] == self.estop_key and payload[1] in KEY_TRIP_EVENTS:
                            self.trip(f'key{self.estop_key}')
            except Exception:
                if not rclpy.ok():
                    return
                raise

    def calibrate(self, g):
        now = time.monotonic()
        if self.calib_t0 is None:
            self.calib_t0 = now
        self.calib.append(g)
        if now - self.calib_t0 < self.calib_s:
            return False
        stds = [statistics.pstdev(c[i] for c in self.calib) for i in range(3)]
        if max(stds) <= self.calib_max_std:
            self.gyro.bias = [statistics.mean(c[i] for c in self.calib) for i in range(3)]
            self.get_logger().info('gyro bias (deg/s) %s from %d samples, std %s' % (
                [round(b, 3) for b in self.gyro.bias], len(self.calib), [round(x, 3) for x in stds]))
        else:
            self.get_logger().warn('robot not stationary during gyro calibration (std %s deg/s); '
                                   'publishing uncorrected gyro' % [round(x, 3) for x in stds])
        self.calib = None
        return True

    def publish_imu(self, payload):
        (ax, ay, az), (gx, gy, gz) = decode_imu(payload)
        if self.calib is not None and not self.calibrate((gx, gy, gz)):
            return
        gx, gy, gz = self.gyro.update((gx, gy, gz), time.monotonic())
        ax, ay, az = ((v - o) * k for v, o, k in zip((ax, ay, az), self.acc_off, self.acc_scale))
        m = Imu()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = self.frame_id
        m.orientation_covariance[0] = -1.0
        m.angular_velocity_covariance[0] = m.angular_velocity_covariance[4] = \
            m.angular_velocity_covariance[8] = self.gyro_var
        m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z = ax * G, ay * G, az * G
        m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z = (
            math.radians(gx), math.radians(gy), math.radians(gz))
        self.imu_pub.publish(m)

    def publish_wheel_odom(self, now, vx, vy, wz):
        vx, vy = vx * self.odom_scale_x, vy * self.odom_scale_y     # odometry only; motors get the command
        dt = 0.0 if self.odom_t is None else min(now - self.odom_t, 0.1)
        self.odom_t = now
        x, y, th = self.odom_pose = integrate_pose(*self.odom_pose, vx, vy, wz, dt)
        m = Odometry()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id, m.child_frame_id = self.odom_frame, self.base_frame
        m.pose.pose.position.x, m.pose.pose.position.y = x, y
        m.pose.pose.orientation.z, m.pose.pose.orientation.w = math.sin(th / 2), math.cos(th / 2)
        m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z = vx, vy, wz
        for i, v in ((0, self.odom_lin_var), (7, self.odom_lin_var), (14, 1e-6), (21, 1e-6), (28, 1e-6),
                     (35, self.odom_ang_var)):
            m.twist.covariance[i] = v
            m.pose.covariance[i] = 1e3 if i in (0, 7, 35) else 1e-6     # pose is dead reckoning: not trusted
        self.odom_pub.publish(m)

    def publish_battery(self, mv):
        m = BatteryState()
        m.header.stamp = self.get_clock().now().to_msg()
        m.voltage = mv / 1000.0
        m.present = True
        m.percentage = float('nan')
        self.bat_pub.publish(m)
        # plugged-in detection (2026-10-01 battery log: plug +249 mV, unplug -226 mV in one step with the wheels OFF;
        # motor load gives +-170 mV with the wheels ON). None = unknown since start.
        now_m = time.monotonic()
        hist = getattr(self, 'bat_hist', [])
        hist = [(t, v) for t, v in hist if now_m - t < 8.0]
        if hist and not self.gate.enabled and now_m - getattr(self, 'wheels_off_t', 0.0) > 3.0:
            base = sorted(v for t, v in hist if now_m - t > 2.0) or [hist[0][1]]
            step = mv - base[len(base) // 2]
            if step >= PLUG_STEP_MV and self.plugged is not True:
                self.plugged = True; self.get_logger().info(f'battery: PLUGGED IN (+{step} mV)'); hist = []
                try:
                    json.dump({'plugged': self.plugged, 't': time.time()}, open(os.path.expanduser('~/battery/plugged.json'), 'w'))
                except OSError:
                    pass
            elif step <= -PLUG_STEP_MV and self.plugged is not False:
                self.plugged = False; self.get_logger().info(f'battery: UNPLUGGED ({step} mV)'); hist = []
                try:
                    json.dump({'plugged': self.plugged, 't': time.time()}, open(os.path.expanduser('~/battery/plugged.json'), 'w'))
                except OSError:
                    pass
        self.bat_hist = hist + [(now_m, mv)]
        # battery log (owner 2026-10-01: learn full charge + the plug/unplug voltage step): one line per 10 s,
        # ~/battery/YYYYMMDD.csv = unix time, mV, wheels enabled, arm enabled (load context for the sag)
        now = time.time()
        if now - getattr(self, 'bat_log_t', 0.0) >= 10.0:
            self.bat_log_t = now
            try:
                d = os.path.expanduser('~/battery'); os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, time.strftime('%Y%m%d') + '.csv'), 'a') as f:
                    f.write(f'{now:.0f},{mv},{int(self.gate.enabled)},{int(self.arm_enabled)}\n')
            except OSError:
                pass

    def stop(self):
        self.running = False
        try:
            self.write(STOP_FRAME)
            self.write(STOP_FRAME)
        finally:
            self.thread.join(timeout=1.0)
            os.close(self.fd)


def main():
    rclpy.init()
    node = BoardDriver()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
