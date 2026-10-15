"""Pure arm motion logic (no ROS, no I/O) - docs/motion_safety.md, Arm.

Minimum-jerk (quintic) profile: s(u) = 10u^3 - 15u^4 + 6u^5, u in [0, 1]; velocity and
acceleration are zero at both ends (smooth start/stop). Peak speed = 1.875 * distance / T.
"""
import struct

from rosorin_base.rrc_protocol import encode_frame

FUNC_BUS_SERVO = 5
QUINTIC_PEAK = 1.875


def smooth(u):
    u = min(1.0, max(0.0, u))
    return u * u * u * (10 - 15 * u + 6 * u * u)


def duration_for(dist, max_speed, min_t=0.2):
    """Trajectory time so the quintic's peak speed stays <= max_speed (pulses/s)."""
    return max(min_t, QUINTIC_PEAK * abs(dist) / max_speed)


class Trajectory:
    def __init__(self, start: dict, goal: dict, max_speed: float, t0: float):
        self.start, self.goal, self.t0 = dict(start), dict(goal), t0
        self.T = max(duration_for(goal[k] - start[k], max_speed) for k in goal)

    def at(self, t):
        s = smooth((t - self.t0) / self.T)
        return {k: int(round(self.start[k] + (self.goal[k] - self.start[k]) * s)) for k in self.goal}

    def done(self, t):
        return t - self.t0 >= self.T


def check_goal(goal: dict, limits: dict):
    """Return None if every joint goal is inside its [lo, hi] limit, else a reason string."""
    for k, v in goal.items():
        if k not in limits:
            return f'servo {k} has no limits configured'
        lo, hi = limits[k]
        if not lo <= v <= hi:
            return f'servo {k} goal {v} outside limits [{lo}, {hi}]'
    return None


def fill_optional(pose: dict, last: dict, optional=()):
    """A read pose {id: pulse or None} with every silent OPTIONAL servo carried at its last known pulse.
    Returns (pose, silent ids). A silent servo that is not optional stays None: its position is unknown."""
    silent = [sid for sid, v in pose.items() if v is None]
    out = dict(pose)
    for sid in silent:
        if sid in optional and last.get(sid) is not None:
            out[sid] = int(round(last[sid]))
    return out, silent


def torque_all(torque: dict, want: int, optional=()):
    """Every servo reports torque state `want`; a silent optional servo does not count."""
    return all(v == want for sid, v in torque.items() if not (v is None and sid in optional))


def move_frame(targets: dict, duration_ms: int):
    """func 5 sub 0x01: duration u16, n, (id u8, pulse u16) x n."""
    p = struct.pack('<BHB', 0x01, int(duration_ms), len(targets))
    for sid, pulse in sorted(targets.items()):
        p += struct.pack('<BH', int(sid), int(pulse))
    return encode_frame(FUNC_BUS_SERVO, p)


def stop_frame(ids):
    """func 5 sub 0x03: n, ids - servos hold where they are."""
    ids = sorted(ids)
    return encode_frame(FUNC_BUS_SERVO, bytes([0x03, len(ids)] + ids))


RAD_PER_PULSE = 0.0041887902047863905      # model/robot_model.json pulse_to_rad (factory, flipped)
JOINT_IDS = (1, 2, 3, 4, 5)


def pulses_to_joints(pulses: dict):
    """{servo id: pulse} -> (['joint1'..'joint5'], [rad]) for URDF joints; None if any joint unknown."""
    if any(pulses.get(sid) is None for sid in JOINT_IDS):
        return None
    return [f'joint{sid}' for sid in JOINT_IDS], [(500 - pulses[sid]) * RAD_PER_PULSE for sid in JOINT_IDS]


def joints_to_pulses(names, positions):
    """Inverse of pulses_to_joints: 'jointN' radians -> {N: pulse}."""
    out = {}
    for n, rad in zip(names, positions):
        if not n.startswith('joint') or not n[5:].isdigit():
            raise ValueError(f'unknown joint {n}')
        out[int(n[5:])] = int(round(500 - rad / RAD_PER_PULSE))
    return out


class WaypointTrajectory:
    """Timed waypoints (MoveIt FollowJointTrajectory, already smooth + time-parameterized) streamed like Trajectory:
    linear in pulses between waypoints. `start` is the current pose (time 0). If any segment would exceed max_speed
    (pulses/s) the WHOLE timeline is stretched - slower, never rejected for speed."""

    def __init__(self, start: dict, points: list, max_speed: float, t0: float):
        # points: [(t_from_start_s, {sid: pulse})], t strictly increasing, t > 0
        self.t0 = t0
        self.pts = [(0.0, {k: start[k] for k in points[0][1]})] + [(t, dict(p)) for t, p in points]
        scale = 1.0
        for (ta, a), (tb, b) in zip(self.pts, self.pts[1:]):
            dt = max(tb - ta, 1e-3)
            v = max(abs(b[k] - a[k]) for k in b) / dt
            scale = max(scale, v / max_speed)
        self.scale = scale
        self.pts = [(t * scale, p) for t, p in self.pts]
        self.T = self.pts[-1][0]
        self.goal = self.pts[-1][1]

    def at(self, t):
        s = t - self.t0
        if s >= self.T:
            return dict(self.goal)
        for (ta, a), (tb, b) in zip(self.pts, self.pts[1:]):
            if s <= tb:
                f = (s - ta) / max(tb - ta, 1e-6)
                return {k: int(round(a[k] + (b[k] - a[k]) * f)) for k in b}
        return dict(self.goal)

    def done(self, t):
        return t - self.t0 >= self.T

    def samples(self):
        """Every waypoint plus each segment midpoint (for the collision veto)."""
        out = [p for _, p in self.pts]
        for (_, a), (_, b) in zip(self.pts, self.pts[1:]):
            out.append({k: int(round((a[k] + b[k]) / 2)) for k in b})
        return out
