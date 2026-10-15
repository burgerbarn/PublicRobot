"""Gamepad -> cmd_vel. Reads board_driver's raw `gamepad` Joy (axes = raw lx, ly, rx, ry / 127, hat).
Stick layout = factory default (joystick_control.py / SDK get_gamepad, read as reference 2026-09-28):
left stick up/down = forward/back, left stick left/right = strafe, right stick left/right = turn.
Buttons are NOT used here: board_driver latches the e-stop on any button.
Publishes only while gamepad frames arrive; if they stop, board_driver's deadman stops the wheels."""
import rclpy
from geometry_msgs.msg import Twist
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Joy

from rosorin_base.motion import joy_to_twist


class GamepadTeleop(Node):
    def __init__(self):
        super().__init__('gamepad_teleop')
        self.max_lin = self.declare_parameter('max_linear', 0.2).value
        self.max_ang = self.declare_parameter('max_angular', 0.8).value
        self.deadzone = self.declare_parameter('deadzone', 0.1).value
        self.pub = self.create_publisher(Twist, 'cmd_vel', 10)
        self.create_subscription(Joy, 'gamepad', self.on_joy, 10)
        self.get_logger().info(f'gamepad teleop: max {self.max_lin} m/s, {self.max_ang} rad/s; '
                               'any button = e-stop (board_driver)')

    def on_joy(self, j):
        if len(j.axes) < 3:
            return
        m = Twist()
        m.linear.x, m.linear.y, m.angular.z = joy_to_twist(j.axes, self.max_lin, self.max_ang, self.deadzone)
        self.pub.publish(m)


def main():
    rclpy.init()
    node = GamepadTeleop()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
