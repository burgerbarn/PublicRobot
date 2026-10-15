#!/usr/bin/env python3
"""Simulated rebuild: joins Gazebo to the emulated hardware in the robot container.
  Gazebo LiDAR (/sim/lidar via ros_gz_bridge) -> UDP JSON -> fake_lidar.py (robot container, port 5601)
  fake_board.py wheel commands (UDP JSON {vx, vy, wz}, port 5602) -> /model/rosorin/cmd_vel -> Gazebo body
Runs inside lbsim-gz on ROS domain 99, localhost only: nothing here is visible to the robot's ROS graph.
If the board goes quiet for 0.5 s the body is stopped (the emulated board itself has no watchdog, like the real one;
this only covers a dead emulator)."""
import json, os, socket, threading, time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Twist

ROBOT = os.environ.get('LBSIM_ROBOT', 'lbsim-robot')


class Relay(Node):
    def __init__(self):
        super().__init__('lbsim_gz_relay')
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.create_subscription(LaserScan, '/sim/lidar', self.on_scan, 5)
        self.pub = self.create_publisher(Twist, '/model/rosorin/cmd_vel', 5)
        self.last = 0.0
        self.scans = 0
        threading.Thread(target=self.twist_in, daemon=True).start()
        self.create_timer(0.1, self.watch)

    def on_scan(self, m):
        msg = json.dumps({'angle_min': m.angle_min, 'angle_increment': m.angle_increment,
                          'ranges': [r if r == r and r != float('inf') else None for r in m.ranges]})
        try:
            self.out.sendto(msg.encode(), (ROBOT, 5601))
            self.scans += 1
        except OSError:
            pass                                  # robot container not up yet

    def twist_in(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('0.0.0.0', 5602))
        while True:
            d = json.loads(s.recv(4096))
            t = Twist()
            t.linear.x, t.linear.y, t.angular.z = float(d['vx']), float(d['vy']), float(d['wz'])
            self.pub.publish(t)
            self.last = time.monotonic()

    def watch(self):
        if self.last and time.monotonic() - self.last > 0.5:
            self.pub.publish(Twist())
            self.last = 0.0


rclpy.init()
rclpy.spin(Relay())
