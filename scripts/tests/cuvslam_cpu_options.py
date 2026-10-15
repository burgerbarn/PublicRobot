"""What does each cuVSLAM option cost in CPU on THIS robot, live camera, robot standing still? Runs its own tracker
next to the running vslam_odom (subscribes to the camera, publishes nothing) once per option set for SECS seconds and
prints CPU seconds used per frame by this process. 2026-10-05: vslam_odom alone uses 2.0 of 8 cores while idle.
Usage: python3 cuvslam_cpu_options.py [secs per variant, default 15]"""
import resource, sys, time

import numpy as np
import rclpy
from message_filters import ApproximateTimeSynchronizer, Subscriber
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image

import cuvslam as vslam

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
M = vslam.Tracker.MulticameraMode
VARIANTS = [('as deployed', {}),
            ('no exports', dict(enable_observations_export=False, enable_landmarks_export=False)),
            ('no exports, sync sba', dict(enable_observations_export=False, enable_landmarks_export=False, async_sba=False)),
            ('no exports, no motion model', dict(enable_observations_export=False, enable_landmarks_export=False,
                                                 use_motion_model=False)),
            ('no exports, performance', dict(enable_observations_export=False, enable_landmarks_export=False,
                                             multicam_mode=M.Performance)),
            ('no exports, grey image', dict(enable_observations_export=False, enable_landmarks_export=False)),
            ('use_gpu False', dict(use_gpu=False))]

rclpy.init()
n = rclpy.create_node('cuvslam_cpu_options')
st = {'info': None, 'frames': [], 'tracker': None, 'grey': False, 'ok': 0}
n.create_subscription(CameraInfo, '/aurora/rgb/camera_info', lambda m: st.__setitem__('info', m), 1)


def on_pair(ir, dp):
    if st['tracker'] is None:
        return
    img = np.frombuffer(ir.data, np.uint8).reshape(ir.height, ir.width, -1)[:, :, :3]
    img = np.ascontiguousarray(img[:, :, 1] if st['grey'] else img)
    dep = np.ascontiguousarray(np.frombuffer(dp.data, np.uint16).reshape(dp.height, dp.step // 2)[:, :dp.width])
    t = ir.header.stamp.sec * 1_000_000_000 + ir.header.stamp.nanosec
    t0 = time.perf_counter()
    try:
        est, _ = st['tracker'].track(t, images=[img], depths=[dep])
        st['ok'] += est.world_from_rig is not None
    except Exception as e:
        st['err'] = str(e)[:80]
    st['frames'].append(time.perf_counter() - t0)


a = Subscriber(n, Image, '/aurora/rgb/image_raw', qos_profile=qos_profile_sensor_data)
b = Subscriber(n, Image, '/aurora/depth/image_raw', qos_profile=qos_profile_sensor_data)
sync = ApproximateTimeSynchronizer([a, b], 10, 0.02); sync.registerCallback(on_pair)
while st['info'] is None:
    rclpy.spin_once(n, timeout_sec=0.2)
i = st['info']


def tracker(opts):
    cam = vslam.Camera()
    cam.distortion = (vslam.Distortion(vslam.Distortion.Model.Brown, list(i.d[:5])) if len(i.d) >= 5
                      else vslam.Distortion(vslam.Distortion.Model.Pinhole))
    cam.focal = i.k[0], i.k[4]; cam.principal = i.k[2], i.k[5]; cam.size = i.width, i.height
    rgbd = vslam.Tracker.OdometryRGBDSettings(); rgbd.depth_scale_factor = 1000.0; rgbd.depth_camera_id = 0
    rgbd.enable_depth_stereo_tracking = False
    return vslam.Tracker(vslam.Rig([cam]), vslam.Tracker.OdometryConfig(
        odometry_mode=vslam.Tracker.OdometryMode.RGBD, rgbd_settings=rgbd, **opts))


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


print(f'{"variant":30s} {"frames":>6s} {"fps":>5s} {"tracked":>7s} {"cores":>6s} {"cpu ms/frame":>12s} {"track() ms":>10s}')
for name, opts in VARIANTS:
    st.update(frames=[], ok=0, grey='grey' in name); st.pop('err', None)
    try:
        st['tracker'] = tracker(opts)
    except Exception as e:
        print(f'{name:30s} not accepted: {str(e)[:90]}'); continue
    end = time.time() + 3
    while time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.05)            # warm up
    st.update(frames=[], ok=0)
    c0, t0 = cpu(), time.time()
    while time.time() - t0 < SECS:
        rclpy.spin_once(n, timeout_sec=0.05)
    c, dt, k = cpu() - c0, time.time() - t0, len(st['frames'])
    st['tracker'] = None
    time.sleep(1.0)
    print(f'{name:30s} {k:6d} {k / dt:5.1f} {st["ok"]:7d} {c / dt:6.2f} {1000 * c / max(k, 1):12.1f} '
          f'{1000 * float(np.mean(st["frames"] or [0])):10.1f} {st.get("err", "")}', flush=True)
