"""Read-only bridge: controller board -> ROS 2. Opens the port O_RDONLY; never writes to the board."""
import fcntl
import math
import os
import select
import statistics
import termios
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu

from rosorin_base.rrc_protocol import (FUNC_IMU, FUNC_SYS, FrameParser, decode_battery_mv,
                                       decode_imu)

G = 9.80665


def open_raw_readonly(dev, baud):
    fd = os.open(dev, os.O_RDONLY | os.O_NOCTTY)
    fcntl.ioctl(fd, termios.TIOCEXCL)          # single owner: further opens get EBUSY
    a = termios.tcgetattr(fd)
    a[0] = 0                                   # iflag
    a[1] = 0                                   # oflag
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[3] = 0                                   # lflag
    a[4] = a[5] = baud
    a[6][termios.VMIN] = 1
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd


class BoardReader(Node):
    def __init__(self):
        super().__init__('rosorin_board_reader')
        self.dev = self.declare_parameter('device', '/dev/ttyACM0').value
        self.frame_id = self.declare_parameter('imu_frame_id', 'imu_link').value
        self.calib_s = self.declare_parameter('gyro_calib_seconds', 3.0).value
        self.calib_max_std = self.declare_parameter('gyro_calib_max_std_dps', 0.3).value
        self.gyro_bias = [0.0, 0.0, 0.0]
        self.calib = []
        self.calib_t0 = None
        self.imu_pub = self.create_publisher(Imu, 'imu/data_raw', 10)
        self.bat_pub = self.create_publisher(BatteryState, 'battery', 10)
        self.parser = FrameParser()
        self.fd = open_raw_readonly(self.dev, termios.B1000000)
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        self.get_logger().info(f'reading {self.dev} (read-only)')

    def loop(self):
        while self.running:
            ready, _, _ = select.select([self.fd], [], [], 0.2)   # wake up to check self.running
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
            except Exception:
                if not rclpy.ok():      # context shut down by the signal handler: stop quietly
                    return
                raise

    def calibrate(self, g):
        """Collect gyro samples for calib_s at startup; accept the mean only if stationary."""
        now = time.monotonic()
        if self.calib_t0 is None:
            self.calib_t0 = now
        self.calib.append(g)
        if now - self.calib_t0 < self.calib_s:
            return False
        stds = [statistics.pstdev(c[i] for c in self.calib) for i in range(3)]
        if max(stds) <= self.calib_max_std:
            self.gyro_bias = [statistics.mean(c[i] for c in self.calib) for i in range(3)]
            self.get_logger().info(
                'gyro bias (deg/s) %s from %d samples, std %s' % (
                    [round(b, 3) for b in self.gyro_bias], len(self.calib), [round(x, 3) for x in stds]))
        else:
            self.get_logger().warn(
                'robot not stationary during gyro calibration (std %s deg/s); publishing uncorrected gyro'
                % [round(x, 3) for x in stds])
        self.calib = None
        return True

    def publish_imu(self, payload):
        (ax, ay, az), (gx, gy, gz) = decode_imu(payload)
        if self.calib is not None and not self.calibrate((gx, gy, gz)):
            return
        gx, gy, gz = gx - self.gyro_bias[0], gy - self.gyro_bias[1], gz - self.gyro_bias[2]
        m = Imu()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = self.frame_id
        m.orientation_covariance[0] = -1.0      # no orientation estimate
        m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z = ax * G, ay * G, az * G
        m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z = (
            math.radians(gx), math.radians(gy), math.radians(gz))
        self.imu_pub.publish(m)

    def publish_battery(self, mv):
        m = BatteryState()
        m.header.stamp = self.get_clock().now().to_msg()
        m.voltage = mv / 1000.0
        m.present = True
        m.percentage = float('nan')
        self.bat_pub.publish(m)

    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)
        os.close(self.fd)


def main():
    rclpy.init()
    node = BoardReader()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop()                 # reader thread stopped before the node is destroyed
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
