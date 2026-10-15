"""Read-only COIN-D6 LiDAR -> sensor_msgs/LaserScan. Opens the port O_RDONLY; sends nothing."""
import fcntl
import math
import os
import select
import termios
import threading
import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.duration import Duration
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

from rosorin_base.coin_d6 import PacketParser, decode

BINS = 400                          # 0.9 deg, matches the measured ~401 samples/ring


class LidarReader(Node):
    def __init__(self):
        super().__init__('rosorin_lidar_reader')
        self.dev = self.declare_parameter('device', '/dev/lidar').value
        self.frame_id = self.declare_parameter('frame_id', 'laser').value
        # Native angles run clockwise: launch sets clockwise=True (floor test 2026-09-28, docs/hardware.md)
        self.offset = self.declare_parameter('angle_offset_deg', 0.0).value
        self.clockwise = self.declare_parameter('clockwise', False).value
        self.range_min = self.declare_parameter('range_min', 0.12).value   # masks self-returns (<=106 mm)
        self.range_max = self.declare_parameter('range_max', 8.0).value
        self.pub = self.create_publisher(LaserScan, 'scan', 10)
        # 2026-10-12: rf2o ignores range_min and matched the ~52 self-returns (0.05-0.11 m) as the room: 8 cm of a
        # true 80 cm on the floor; the same scan with them set to inf read 86 cm. rf2o reads this copy (rf2o.yaml).
        self.pub_rf2o = self.create_publisher(LaserScan, 'scan_rf2o', 10)
        self.parser = PacketParser()
        self.stale_s = self.declare_parameter('reopen_after_silence_s', 2.0).value
        self.fd = self.open_port()
        self.ring = None
        self.ring_t = None
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        self.get_logger().info(f'reading {self.dev} (read-only)')

    def open_port(self):
        fd = os.open(self.dev, os.O_RDONLY | os.O_NOCTTY)
        fcntl.ioctl(fd, termios.TIOCEXCL)
        a = termios.tcgetattr(fd)
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = termios.B230400
        a[6][termios.VMIN] = 1
        a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        return fd

    def reopen(self, why):
        """USB re-enumeration (seen 2026-09-28: EPIPE, ttyUSB1 -> ttyUSB0) leaves the old fd dead."""
        self.get_logger().warn(f'{self.dev}: {why}; reopening')
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd, self.ring, self.parser = None, None, PacketParser()
        while self.running:
            try:
                self.fd = self.open_port()
                self.get_logger().info(f'{self.dev} reopened')
                return
            except OSError:
                time.sleep(0.5)

    def loop(self):
        last_data = time.monotonic()
        while self.running:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.2)
                data = os.read(self.fd, 1024) if ready else None
            except OSError as e:
                self.reopen(f'read error {e}')
                last_data = time.monotonic()
                continue
            if data == b'':
                self.reopen('EOF (device gone)')
                last_data = time.monotonic()
                continue
            if not data:
                if time.monotonic() - last_data > self.stale_s:
                    self.reopen(f'no data for {self.stale_s:.0f} s')
                    last_data = time.monotonic()
                continue
            last_data = time.monotonic()
            try:
                for pkt in self.parser.feed(data):
                    start, pts = decode(pkt)
                    if start:
                        now = time.monotonic()
                        if self.ring:
                            self.publish(self.ring, now - self.ring_t)
                        self.ring, self.ring_t = [], now
                    elif self.ring is not None:
                        self.ring.extend(pts)
            except Exception as e:
                if not rclpy.ok():
                    return
                # a dead reader thread would leave the node up and silent for good: exit, launch respawns the node
                # (base.launch.py respawn=True); meanwhile the base driver's scan gate holds the wheels
                self.get_logger().error(f'reader thread failed: {e!r}; exiting for a restart')
                os._exit(1)

    def publish(self, pts, dt):
        ranges = [float('inf')] * BINS
        inten = [0.0] * BINS
        for a, mm, i in pts:
            if mm == 0:
                continue
            ang = (-a if self.clockwise else a) + self.offset
            b = int(round((ang % 360.0) / 360.0 * BINS)) % BINS
            r = mm / 1000.0
            if r < ranges[b]:
                ranges[b] = r
                inten[b] = float(i)
        m = LaserScan()
        # REP/LaserScan: stamp = first ray. Ring is published when the next ring starts, so the first
        # ray was dt ago. (Stamping 'now' put every scan ~100 ms ahead of TF: AMCL/costmap dropped scans,
        # Nav2 collision 2026-09-28.)
        m.header.stamp = (self.get_clock().now() - Duration(seconds=dt)).to_msg()
        m.header.frame_id = self.frame_id
        m.angle_min = 0.0
        m.angle_increment = 2 * math.pi / BINS
        m.angle_max = m.angle_increment * (BINS - 1)
        m.scan_time = dt
        m.time_increment = dt / BINS
        m.range_min = self.range_min
        m.range_max = self.range_max
        m.ranges = ranges
        m.intensities = inten
        self.pub.publish(m)
        m.ranges = [float('inf') if r < self.range_min else r for r in ranges]
        self.pub_rf2o.publish(m)

    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)
        if self.fd is not None:
            os.close(self.fd)


def main():
    rclpy.init()
    node = LidarReader()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
