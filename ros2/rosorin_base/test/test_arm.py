from rosorin_base.arm import Trajectory, check_goal, duration_for, move_frame, smooth, stop_frame
from rosorin_base.rrc_protocol import FrameParser


def test_smooth_endpoints_and_monotonic():
    assert smooth(0) == 0 and smooth(1) == 1 and abs(smooth(0.5) - 0.5) < 1e-12
    vals = [smooth(i / 100) for i in range(101)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))


def test_soft_start_and_stop():
    # first and last 5% of the trajectory move < 1% of the distance (zero vel/acc at ends)
    assert smooth(0.05) < 0.01 and 1 - smooth(0.95) < 0.01


def test_peak_speed_respects_cap():
    dist, cap = 200, 50.0
    T = duration_for(dist, cap)
    # continuous profile: peak speed = dist * max ds/du / T  (quintic ds/du max = 1.875 at u=0.5)
    n = 10000
    peak = max(dist * (smooth((i + 1) / n) - smooth(i / n)) * n / T for i in range(n))
    assert peak <= cap * 1.0001
    # as actually streamed: 40 ms steps of integer pulses, allow 1 pulse rounding per step
    tr = Trajectory({1: 500}, {1: 700}, cap, t0=0.0)
    steps = int(T / 0.04) + 1
    pos = [tr.at(i * 0.04)[1] for i in range(steps + 1)]
    assert max(abs(b - a) for a, b in zip(pos, pos[1:])) <= cap * 0.04 + 1


def test_trajectory_reaches_goal_all_joints_together():
    tr = Trajectory({1: 500, 2: 724}, {1: 520, 2: 700}, 50.0, t0=10.0)
    assert tr.at(10.0) == {1: 500, 2: 724}
    assert tr.done(10.0 + tr.T) and tr.at(10.0 + tr.T) == {1: 520, 2: 700}


def test_limits_refuse_not_clamp():
    lim = {1: (450, 550)}
    assert check_goal({1: 520}, lim) is None
    assert 'outside' in check_goal({1: 600}, lim)
    assert 'no limits' in check_goal({2: 500}, lim)


def test_frames_parse_with_our_parser():
    for f in (move_frame({1: 520, 10: 499}, 40), stop_frame([1, 2, 3, 4, 5, 10])):
        out = FrameParser().feed(f)
        assert len(out) == 1 and out[0][0] == 5
    assert move_frame({1: 520}, 40)[4:-1] == bytes([0x01, 40, 0, 1, 1]) + (520).to_bytes(2, 'little')


def test_pulses_to_joints():
    from rosorin_base.arm import pulses_to_joints
    import math
    names, q = pulses_to_joints({1: 500, 2: 500, 3: 500, 4: 500, 5: 500, 10: 300})
    assert names == ['joint1', 'joint2', 'joint3', 'joint4', 'joint5'] and q == [0.0] * 5
    _, q = pulses_to_joints({1: 500 - 375, 2: 500, 3: 500, 4: 500, 5: 500})
    assert abs(q[0] - math.radians(90)) < 1e-3                      # 375 pulses = 90 deg (factory 240 deg / 1000)
    assert pulses_to_joints({1: 500, 2: 500}) is None


def test_joints_to_pulses_roundtrip():
    from rosorin_base.arm import joints_to_pulses, pulses_to_joints
    p = {1: 500, 2: 793, 3: 51, 4: 160, 5: 500}
    assert joints_to_pulses(*pulses_to_joints(p)) == p


def test_waypoint_trajectory_interpolates_and_ends():
    from rosorin_base.arm import WaypointTrajectory
    w = WaypointTrajectory({1: 500, 2: 860}, [(1.0, {1: 600, 2: 860}), (2.0, {1: 600, 2: 760})], 250.0, 0.0)
    assert w.at(0.5) == {1: 550, 2: 860} and w.at(1.5) == {1: 600, 2: 810} and w.at(5) == {1: 600, 2: 760}
    assert w.done(2.0) and not w.done(1.9) and w.scale == 1.0


def test_waypoint_trajectory_too_fast_is_stretched_not_rejected():
    from rosorin_base.arm import WaypointTrajectory
    w = WaypointTrajectory({1: 500}, [(0.1, {1: 600})], 250.0, 0.0)       # 1000 p/s requested
    assert abs(w.scale - 4.0) < 1e-9 and abs(w.T - 0.4) < 1e-9            # -> 250 p/s


# 2026-10-05: the claw servo (10) stopped answering and every pose move refused. An optional servo that is silent is
# carried at its last known pulse; a silent joint stays unknown.
def test_silent_optional_servo_is_carried_at_last_known():
    from rosorin_base.arm import fill_optional
    raw = {1: 54, 2: 962, 3: 46, 4: 133, 5: 500, 10: None}
    pose, silent = fill_optional(raw, {10: 500.4, 1: 1}, optional=(10,))
    assert silent == [10]
    assert pose == {1: 54, 2: 962, 3: 46, 4: 133, 5: 500, 10: 500}


def test_silent_joint_servo_stays_unknown():
    from rosorin_base.arm import fill_optional
    pose, silent = fill_optional({1: None, 2: 962, 10: None}, {1: 500, 10: 500}, optional=(10,))
    assert silent == [1, 10]
    assert pose[1] is None and pose[10] == 500


def test_all_answering_changes_nothing():
    from rosorin_base.arm import fill_optional
    raw = {1: 54, 2: 962, 10: 631}
    assert fill_optional(raw, {10: 500}, optional=(10,)) == (raw, [])


def test_torque_check_ignores_only_a_silent_optional_servo():
    from rosorin_base.arm import torque_all
    assert torque_all({1: 0, 2: 0, 10: None}, 0, optional=(10,))
    assert not torque_all({1: 0, 2: None, 10: None}, 0, optional=(10,))
    assert not torque_all({1: 0, 2: 0, 10: 1}, 0, optional=(10,))
