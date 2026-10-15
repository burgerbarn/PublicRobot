from rosorin_base.gyro_bias import GyroBiasTracker


def run(tr, g, t0, secs, dt=0.01):
    t = t0
    while t < t0 + secs:
        out = tr.update(g, t); t += dt
    return out, t


def test_tracks_only_when_still_and_settled():
    tr = GyroBiasTracker(bias=(0, 0, 0.1), tau=5.0, settle=1.0)
    run(tr, (0, 0, 0.2), 0.0, 10.0)                   # never commanded still -> unchanged
    assert tr.bias[2] == 0.1
    tr.set_commanded_still(True, 10.0)
    run(tr, (0, 0, 0.2), 10.0, 0.9)                   # within settle -> unchanged
    assert tr.bias[2] == 0.1
    out, _ = run(tr, (0, 0, 0.2), 10.9, 60.0)         # 12 tau -> converged
    assert abs(tr.bias[2] - 0.2) < 1e-4 and abs(out[2]) < 1e-4


def test_rejects_real_rotation_and_motion():
    tr = GyroBiasTracker(bias=(0, 0, 0.0), tau=5.0, settle=0.0, max_dev=0.5)
    tr.set_commanded_still(True, 0.0)
    out, t = run(tr, (0, 0, 10.0), 0.0, 5.0)          # pushed by hand: 10 deg/s ignored
    assert tr.bias[2] == 0.0 and out[2] == 10.0
    tr.set_commanded_still(False, t)
    run(tr, (0, 0, 0.3), t, 5.0)                      # driving -> no update
    assert tr.bias[2] == 0.0
