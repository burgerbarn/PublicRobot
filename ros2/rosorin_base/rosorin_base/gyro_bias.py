"""Gyro bias tracking while the robot is commanded still (pure logic, no ROS).

Startup calibration gives the initial bias; the residual wanders (measured 2026-09-27: +-0.01 deg/s
over tens of seconds -> EKF heading drift equal to the gyro integral). While the commanded velocity
has been zero for `settle` s AND every axis reads within `max_dev` of the current bias (rejects
the robot being pushed/turned by hand), the bias follows the reading with time constant `tau`.
"""


class GyroBiasTracker:
    def __init__(self, bias=(0.0, 0.0, 0.0), tau=20.0, settle=1.0, max_dev=0.5):
        self.bias = list(bias)
        self.tau, self.settle, self.max_dev = tau, settle, max_dev
        self.still_since = None
        self.last_t = None

    def set_commanded_still(self, still, now):
        if not still:
            self.still_since = None
        elif self.still_since is None:
            self.still_since = now

    def tracking(self, now):
        return self.still_since is not None and now - self.still_since >= self.settle

    def update(self, g, now):
        """g: raw gyro (deg/s, 3 axes). Returns bias-corrected gyro."""
        dt = 0.0 if self.last_t is None else now - self.last_t
        self.last_t = now
        if self.tracking(now) and dt > 0 and all(abs(x - b) <= self.max_dev for x, b in zip(g, self.bias)):
            a = min(dt / self.tau, 1.0)
            self.bias = [b + a * (x - b) for x, b in zip(g, self.bias)]
        return tuple(x - b for x, b in zip(g, self.bias))
