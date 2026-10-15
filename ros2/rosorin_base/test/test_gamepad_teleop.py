from rosorin_base.motion import joy_to_twist


def test_factory_layout():
    # raw lx>0 -> factory axes lx = -raw -> linear.y negative (strafe right)
    assert joy_to_twist([1.0, 0, 0, 0], 0.2, 0.8) == (0.0, -0.2, -0.0)
    assert joy_to_twist([0, 1.0, 0, 0], 0.2, 0.8) == (0.2, -0.0, -0.0)   # raw ly>0 -> forward
    assert joy_to_twist([0, 0, 1.0, 0], 0.2, 0.8) == (0.0, -0.0, -0.8)   # raw rx>0 -> turn right


def test_deadzone_and_clamp():
    assert joy_to_twist([0.05, -0.09, 0.0, 0], 0.2, 0.8) == (0.0, 0.0, 0.0)
    assert joy_to_twist([0, -128 / 127, 0, 0], 0.2, 0.8)[0] == -0.2      # -128 raw clamps to -1
