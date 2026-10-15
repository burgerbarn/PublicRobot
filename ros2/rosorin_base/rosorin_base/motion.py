"""Pure motion logic (no ROS, no I/O) - see docs/motion_safety.md."""
import math
import struct

from rosorin_base.rrc_protocol import crc8_maxim

FUNC_MOTOR = 3


def clamp(v, lim):
    return max(-lim, min(lim, v))


def ramp(cur, tgt, max_step):
    return cur + clamp(tgt - cur, max_step)


def joy_to_twist(axes, max_lin, max_ang, deadzone=0.1):
    """Gamepad axes, raw order (lx, ly, rx, ry, ...) each raw/127 -> (vx, vy, wz). Factory default layout."""
    lx, ly, rx = (0.0 if abs(v) < deadzone else max(-1.0, min(1.0, v)) for v in axes[:3])
    return ly * max_lin, -lx * max_lin, -rx * max_ang


def integrate_pose(x, y, th, vx, vy, wz, dt):
    """Advance a planar pose by body velocity over dt (midpoint heading)."""
    h = th + wz * dt / 2.0
    c, s = math.cos(h), math.sin(h)
    x += (vx * c - vy * s) * dt
    y += (vx * s + vy * c) * dt
    th = math.atan2(math.sin(th + wz * dt), math.cos(th + wz * dt))
    return x, y, th


def mecanum_rps(vx, vy, wz, wheel_d=0.08, wheelbase=0.17706, track=0.17165):
    """Body velocity (m/s, m/s, rad/s) -> wheel rps for motors 1..4 (factory layout/signs)."""
    k = (wheelbase + track) / 2.0
    m1 = vx - vy - wz * k
    m2 = vx + vy - wz * k
    m3 = vx + vy + wz * k
    m4 = vx - vy + wz * k
    c = math.pi * wheel_d
    return [m1 / c, m2 / c, -m3 / c, -m4 / c]


def motor_frame(rps):
    """rps for motors 1..4 -> board frame (func 3, sub 0x01, wire ids 0..3)."""
    payload = bytes([0x01, len(rps)]) + b''.join(struct.pack('<Bf', i, float(r)) for i, r in enumerate(rps))
    body = bytes([FUNC_MOTOR, len(payload)]) + payload
    return b'\xaa\x55' + body + bytes([crc8_maxim(body)])


STOP_FRAME = motor_frame([0.0, 0.0, 0.0, 0.0])


class MotionGate:
    """Decides the commanded body velocity each tick. Safety stops are immediate (no ramp)."""

    def __init__(self, max_lin=0.2, max_ang=1.0, acc_lin=0.5, acc_ang=2.0, timeout=0.3, sense_timeout=0.0):
        self.max_lin, self.max_ang = max_lin, max_ang
        self.acc_lin, self.acc_ang = acc_lin, acc_ang
        self.timeout = timeout
        # Sensing gate (2026-10-02): commands from software (cmd_vel) move the wheels only while the obstacle sensor
        # is alive. On Humble the Nav2 collision monitor IGNORES a stale scan and passes commands through unchanged
        # (measured, scripts/tests/nav2_isolated_checks.sh), and the contact reflex runs off LiDAR odometry, so a
        # silent LiDAR removed every guard at once. 0 = off. Not latched: motion resumes when the sensor is back.
        # Gamepad commands (manual=True) are exempt: a person holding the pad is looking.
        self.sense_timeout = sense_timeout
        self.sense_t = None
        self.enabled = False            # disabled at start (rule 3)
        self.estop = None               # None or reason string (latched, rule 6)
        self.cmd = (0.0, 0.0, 0.0)
        self.cmd_t = None
        self.cmd_manual = False
        self.cur = (0.0, 0.0, 0.0)

    def set_cmd(self, vx, vy, wz, now, manual=False):
        self.cmd = (vx, vy, wz)
        self.cmd_t = now
        self.cmd_manual = manual

    def sensed(self, now):
        """The obstacle sensor delivered data (the LiDAR published a scan)."""
        self.sense_t = now

    def trip(self, reason):
        if self.estop is None:
            self.estop = reason
        self.enabled = False
        self.cur = (0.0, 0.0, 0.0)

    def reset(self):
        self.estop = None               # motion stays disabled until enabled again

    def enable(self, on):
        if on and self.estop is not None:
            return False
        self.enabled = on
        if not on:
            self.cur = (0.0, 0.0, 0.0)
        return True

    def block_reason(self, now):
        if self.estop is not None:
            return 'estop: ' + self.estop
        if not self.enabled:
            return 'disabled'
        if self.cmd_t is None or now - self.cmd_t > self.timeout:
            return 'cmd timeout'
        if self.sense_timeout > 0 and not self.cmd_manual and (
                self.sense_t is None or now - self.sense_t > self.sense_timeout):
            return 'no scan'
        return None

    def step(self, now, dt):
        if self.block_reason(now) is not None:
            self.cur = (0.0, 0.0, 0.0)
            return self.cur
        vx, vy, wz = self.cmd
        tgt = (clamp(vx, self.max_lin), clamp(vy, self.max_lin), clamp(wz, self.max_ang))
        self.cur = (ramp(self.cur[0], tgt[0], self.acc_lin * dt),
                    ramp(self.cur[1], tgt[1], self.acc_lin * dt),
                    ramp(self.cur[2], tgt[2], self.acc_ang * dt))
        return self.cur
