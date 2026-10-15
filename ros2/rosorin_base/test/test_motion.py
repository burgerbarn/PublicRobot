import math
from rosorin_base.motion import integrate_pose, MotionGate, STOP_FRAME, mecanum_rps, motor_frame
from rosorin_base.rrc_protocol import FrameParser


def test_stop_frame_matches_test1_bytes():
    # exact bytes sent (and observed to stop the wheels) in motion test 1, 2026-09-27
    assert STOP_FRAME.hex() == 'aa5503160104000000000001000000000200000000030000000007'


def test_frame_roundtrip():
    f = motor_frame([0.3, 0.3, 0.3, 0.3])
    assert FrameParser().feed(f) == [(3, f[4:-1])]


def test_kinematics_signs():
    c = math.pi * 0.08
    assert mecanum_rps(0.2, 0, 0) == [0.2 / c, 0.2 / c, -0.2 / c, -0.2 / c]
    w = mecanum_rps(0, 0, 1.0)
    assert w[0] < 0 and w[1] < 0 and w[2] < 0 and w[3] < 0     # rotation: all wire values same sign


def test_disabled_at_start_and_timeout():
    g = MotionGate()
    g.set_cmd(0.1, 0, 0, now=0.0)
    assert g.step(0.0, 0.02) == (0.0, 0.0, 0.0) and g.block_reason(0.0) == 'disabled'
    g.enable(True)
    assert g.step(0.0, 0.02)[0] > 0
    assert g.step(0.31, 0.02) == (0.0, 0.0, 0.0) and g.block_reason(0.31) == 'cmd timeout'


def test_limits_and_ramp():
    g = MotionGate(); g.enable(True)
    t = 0.0
    for _ in range(200):
        g.set_cmd(5.0, -5.0, 9.0, t); v = g.step(t, 0.02); t += 0.02
    assert v == (0.2, -0.2, 1.0)
    g2 = MotionGate(); g2.enable(True); g2.set_cmd(0.2, 0, 0, 0.0)
    assert abs(g2.step(0.0, 0.02)[0] - 0.01) < 1e-9                # 0.5 m/s^2 * 20 ms


def test_estop_latches_and_blocks_enable():
    g = MotionGate(); g.enable(True); g.set_cmd(0.2, 0, 0, 0.0); g.step(0.0, 0.02)
    g.trip('key1')
    assert g.step(0.01, 0.02) == (0.0, 0.0, 0.0)
    assert g.enable(True) is False                                  # refused while latched
    g.reset()
    assert g.block_reason(0.01) == 'disabled'                       # reset does not re-enable
    assert g.enable(True) is True


def test_integrate_pose():
    x, y, th = 0.0, 0.0, 0.0
    for _ in range(100):                                            # 1 s at 0.2 m/s forward
        x, y, th = integrate_pose(x, y, th, 0.2, 0.0, 0.0, 0.01)
    assert abs(x - 0.2) < 1e-9 and abs(y) < 1e-9
    x, y, th = integrate_pose(0.0, 0.0, math.pi / 2, 0.1, 0.0, 0.0, 1.0)   # heading +y
    assert abs(x) < 1e-9 and abs(y - 0.1) < 1e-9
    x, y, th = integrate_pose(0.0, 0.0, 0.0, 0.0, 0.1, 0.0, 1.0)          # strafe left = +y
    assert abs(y - 0.1) < 1e-9
    for _ in range(1000):                                           # full circle, r = v/w
        x, y, th = integrate_pose(x, y, th, 0.0, 0.0, 2 * math.pi / 10, 0.01)
    assert abs(th) < 1e-9


def test_sense_gate_blocks_software_commands_without_a_fresh_scan():
    g = MotionGate(sense_timeout=0.5); g.enable(True)
    g.set_cmd(0.2, 0, 0, now=0.0)
    assert g.step(0.0, 0.02) == (0.0, 0.0, 0.0) and g.block_reason(0.0) == 'no scan'    # never seen a scan
    g.sensed(0.0)
    assert g.step(0.0, 0.02)[0] > 0                                                      # scan alive: moves
    t = 0.0
    while t < 0.45:                                                                      # commands keep coming
        t += 0.02; g.set_cmd(0.2, 0, 0, t); v = g.step(t, 0.02)
    assert v[0] > 0
    t = 0.52; g.set_cmd(0.2, 0, 0, t)                                                    # scan now 0.52 s old
    assert g.step(t, 0.02) == (0.0, 0.0, 0.0) and g.block_reason(t) == 'no scan'         # stop at once, no ramp
    g.sensed(t)                                                                          # LiDAR back: not latched
    assert g.block_reason(t) is None and 0 < g.step(t, 0.02)[0] <= 0.011                 # resumes with the ramp


def test_sense_gate_exempts_the_gamepad_and_is_off_by_default():
    g = MotionGate(sense_timeout=0.5); g.enable(True)
    g.set_cmd(0.2, 0, 0, now=0.0, manual=True)
    assert g.step(0.0, 0.02)[0] > 0 and g.block_reason(0.0) is None
    g.set_cmd(0.2, 0, 0, now=0.02)                                                       # software takes over
    assert g.block_reason(0.02) == 'no scan'
    g0 = MotionGate(); g0.enable(True); g0.set_cmd(0.2, 0, 0, 0.0)
    assert g0.block_reason(0.0) is None
