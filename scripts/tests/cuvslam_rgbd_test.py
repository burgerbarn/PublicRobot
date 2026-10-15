#!/usr/bin/env python3
"""cuVSLAM (NVIDIA PyCuVSLAM v17) RGB-D odometry on the Aurora: IR image (mono8) + depth (mono16 mm), both in
depth_camera_link, 640x400 - pixel-aligned. Follows NVIDIA's examples/realsense/run_rgbd.py. Prints tracking rate,
failures and the camera pose every 2 s. Test only: publishes nothing.
Run on the robot: python3 cuvslam_rgbd_test.py [seconds]"""
import math, sys, time

import numpy as np
import rclpy
from message_filters import ApproximateTimeSynchronizer, Subscriber
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

import cuvslam as vslam
from tf2_ros import Buffer, TransformListener

SRC = sys.argv[2] if len(sys.argv) > 2 else 'rgb'   # image for tracking: rgb (depth is aligned to it) or ir
DEPTH_PER_M = 1000.0                      # mono16 depth in mm


class Test:
    def __init__(self, node, info):
        cam = vslam.Camera()
        cam.distortion = vslam.Distortion(vslam.Distortion.Model.Brown, list(info.d[:5]) if info.d else [0.0] * 5) \
            if info.distortion_model == 'plumb_bob' else vslam.Distortion(vslam.Distortion.Model.Pinhole)
        cam.focal = info.k[0], info.k[4]
        cam.principal = info.k[2], info.k[5]
        cam.size = info.width, info.height
        rgbd = vslam.Tracker.OdometryRGBDSettings()
        rgbd.depth_scale_factor = DEPTH_PER_M
        rgbd.depth_camera_id = 0
        rgbd.enable_depth_stereo_tracking = False
        cfg = vslam.Tracker.OdometryConfig(async_sba=True, odometry_mode=vslam.Tracker.OdometryMode.RGBD,
                                           rgbd_settings=rgbd)
        self.tracker = vslam.Tracker(vslam.Rig([cam]), cfg)
        self.ok = self.fail = 0
        self.dts = []
        self.pose = None
        self.last_t = None
        ir = Subscriber(node, Image, f'/aurora/{SRC}/image_raw', qos_profile=qos_profile_sensor_data)
        dp = Subscriber(node, Image, '/aurora/depth/image_raw', qos_profile=qos_profile_sensor_data)
        self.sync = ApproximateTimeSynchronizer([ir, dp], 10, 0.09 if SRC == 'rgb' else 0.02)
        self.sync.registerCallback(self.cb)

    def cb(self, ir, dp):
        t = ir.header.stamp.sec * 1_000_000_000 + ir.header.stamp.nanosec
        if self.last_t is not None and t <= self.last_t:
            return
        self.last_t = t
        img = np.frombuffer(ir.data, np.uint8).reshape(ir.height, ir.width, -1)
        img = img[:, :, 0] if img.shape[2] == 1 else img[:, :, :3]
        dep = np.frombuffer(dp.data, np.uint16).reshape(dp.height, dp.step // 2)[:, :dp.width]
        t0 = time.time()
        est, _ = self.tracker.track(t, images=[np.ascontiguousarray(img)], depths=[np.ascontiguousarray(dep)])
        self.dts.append(time.time() - t0)
        if est.world_from_rig is None:
            self.fail += 1
        else:
            self.ok += 1
            self.pose = est.world_from_rig.pose


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    rclpy.init()
    node = rclpy.create_node('cuvslam_rgbd_test')
    box = {}
    node.create_subscription(CameraInfo, f'/aurora/{SRC}/camera_info', lambda m: box.setdefault('i', m), 1)
    while 'i' not in box:
        rclpy.spin_once(node, timeout_sec=0.5)
    print('cuVSLAM', vslam.get_version(), SRC, box['i'].header.frame_id, 'camera', box['i'].width, 'x', box['i'].height, box['i'].distortion_model)
    test = Test(node, box['i'])
    buf = Buffer(); TransformListener(buf, node)
    ref = {}

    def tf_cam():                           # camera motion since start per the arm's own TF (base_link fixed on the stand)
        try:
            t = buf.lookup_transform('base_link', box['i'].header.frame_id, rclpy.time.Time()).transform
        except Exception:
            return None
        q, p = t.rotation, np.array([t.translation.x, t.translation.y, t.translation.z])
        x, y, z, w = q.x, q.y, q.z, q.w
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        if 'R' not in ref:
            if test.ok == 0:
                return None
            ref['R'], ref['p'] = R, p
            print('R base<-camera at start:', np.round(R, 3).tolist(), flush=True)
        ref['ang'] = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(ref['R'].T @ R) - 1) / 2))))
        return ref['R'].T @ (p - ref['p'])
    end, nxt = time.time() + secs, time.time() + 0.5
    while time.time() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
        if time.time() >= nxt:
            nxt += 0.5
            p = test.pose
            pos = 'none' if p is None else ' '.join(f'{v:+.3f}' for v in p.translation)
            ms = 1000 * np.mean(test.dts[-20:]) if test.dts else 0
            tc = tf_cam()
            tfs = 'none' if tc is None else ' '.join(f'{v:+.3f}' for v in tc)
            va = 'none' if p is None else f'{math.degrees(2 * math.acos(min(1.0, abs(p.rotation[3])))):6.1f}'
            print(f'tracked {test.ok} failed {test.fail} track {ms:.1f} ms  cuvslam xyz(m) {pos} rot {va} deg | '
                  f'arm TF xyz(m) {tfs} rot {ref.get("ang", 0):6.1f} deg', flush=True)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
