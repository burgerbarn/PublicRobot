#!/usr/bin/env python3
"""Simulated rebuild: Gazebo's RGB-D camera as the Aurora 930 driver publishes it (chapter 15), in the robot's ROS graph.

Runs in lbsim-gz on ROS domain 0 (the robot's), network lbsim. Input: Gazebo's camera bridged to hidden topics
(/_lbsim/...); output, as recorded from the real driver (chapter 15, 2026-09-29/10-02):
  /aurora/rgb/image_raw    640x400 bgr8, frame rgb_camera_link       /aurora/rgb/camera_info
  /aurora/depth/image_raw  640x400 mono16 millimetres, 150-4000 mm as the driver filters (0 = no depth), frame depth_camera_link (pixels follow RGB)
  /aurora/depth/camera_info, /aurora/ir/image_raw (mono8), /aurora/ir/camera_info
  static TF depth_camera_link -> rgb_camera_link (the driver publishes it from the camera's extrinsics; identity here)
Stamps are wall-clock time (Gazebo stamps with its own clock from 0; the robot runs on wall time).
Not modelled: the real camera's depth holes and noise, its 72 ms RGB/depth offset, /aurora/points2.
"""
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo
from geometry_msgs.msg import TransformStamped
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster


class SimAurora(Node):
    def __init__(self):
        super().__init__('sim_aurora930')
        q = qos_profile_sensor_data
        # RELIABLE out, like the real driver must be: the robot's readers use both default (reliable) QoS (mind,
        # vision tools, vslam_odom's camera_info) and sensor-data QoS; a reliable publisher serves both.
        out = 5
        self.pub = {k: self.create_publisher(Image, f'/aurora/{k}/image_raw', out) for k in ('rgb', 'depth', 'ir')}
        self.info = {k: self.create_publisher(CameraInfo, f'/aurora/{k}/camera_info', out) for k in ('rgb', 'depth', 'ir')}
        self.create_subscription(Image, '/_lbsim/camera/image', self.on_rgb, q)
        self.create_subscription(Image, '/_lbsim/camera/depth_image', self.on_depth, q)
        self.create_subscription(CameraInfo, '/_lbsim/camera/camera_info', self.on_info, q)
        self.last_info = None
        t = TransformStamped()
        t.header.stamp = self.get_clock().now().to_msg()
        t.header.frame_id, t.child_frame_id = 'depth_camera_link', 'rgb_camera_link'
        t.transform.rotation.w = 1.0
        self.tf = StaticTransformBroadcaster(self)
        self.tf.sendTransform(t)

    def stamp(self, m, frame):
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = frame
        return m

    def on_info(self, m):
        self.last_info = m

    def send_info(self, kind, frame):
        if self.last_info is not None:
            i = self.last_info
            i.header.stamp = self.get_clock().now().to_msg()
            i.header.frame_id = frame
            self.info[kind].publish(i)

    def on_rgb(self, m):
        a = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, 3)
        out = Image(height=m.height, width=m.width, encoding='bgr8', is_bigendian=0, step=m.width * 3)
        out.data = a[:, :, ::-1].tobytes()
        self.pub['rgb'].publish(self.stamp(out, 'rgb_camera_link'))
        self.send_info('rgb', 'rgb_camera_link')
        ir = Image(height=m.height, width=m.width, encoding='mono8', is_bigendian=0, step=m.width)
        ir.data = (a.astype(np.uint16).sum(axis=2) // 3).astype(np.uint8).tobytes()
        self.pub['ir'].publish(self.stamp(ir, 'depth_camera_link'))
        self.send_info('ir', 'depth_camera_link')

    def on_depth(self, m):
        d = np.frombuffer(m.data, np.float32).reshape(m.height, m.width)
        mm = np.where(np.isfinite(d) & (d >= 0.15) & (d <= 4.0), d * 1000.0, 0.0).astype(np.uint16)
        out = Image(height=m.height, width=m.width, encoding='mono16', is_bigendian=0, step=m.width * 2)   # as the real driver (ch15 capture)
        out.data = mm.tobytes()
        self.pub['depth'].publish(self.stamp(out, 'depth_camera_link'))
        self.send_info('depth', 'depth_camera_link')


rclpy.init()
rclpy.spin(SimAurora())
