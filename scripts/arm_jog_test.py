"""Servo smoothness test on the stand: JointJog pan/tilt velocity profile; records /joint_states."""
import json, sys, time, numpy as np, rclpy
from control_msgs.msg import JointJog
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
rclpy.init(); n = rclpy.create_node('servo_try'); st = {}; js = []
n.create_subscription(String, '/rosorin_board_driver/state', lambda m: st.update(json.loads(m.data)), 5)
n.create_subscription(JointState, '/joint_states', lambda m: js.append((time.time(), list(m.position))), 50)
pub = n.create_publisher(JointJog, '/arm_controller/joint_jog', 10)
def call(name, T, req):
    c = n.create_client(T, name); c.wait_for_service(timeout_sec=5)
    f = c.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=60); return f.result()
t = time.time()
while not st and time.time() - t < 5: rclpy.spin_once(n, timeout_sec=0.1)
if st.get('enabled') or st.get('estop'): sys.exit('refusing')
if not st.get('arm_holding'): print('recover', call('/rosorin_board_driver/arm/recover', Trigger, Trigger.Request()).success)
print('enable', call('/rosorin_board_driver/arm/enable', SetBool, SetBool.Request(data=True)).message)
pass
plan = [('joint1', 0.3, 2.0), ('joint1', -0.3, 4.0), ('joint1', 0.3, 2.0), ('joint4', -0.2, 1.0), ('joint4', 0.2, 1.0)]
js.clear(); t_start = time.time()
try:
    for jn, v, dur in plan:
        end = time.time() + dur
        while time.time() < end:
            m = JointJog(joint_names=[jn], velocities=[v]); m.header.stamp = n.get_clock().now().to_msg(); m.header.frame_id = 'base_link'
            pub.publish(m); rclpy.spin_once(n, timeout_sec=0.02)
            if st.get('estop'): raise RuntimeError('estop')
    end = time.time() + 1.0
    while time.time() < end: rclpy.spin_once(n, timeout_sec=0.02)
finally:
    pass
    print('disable', call('/rosorin_board_driver/arm/enable', SetBool, SetBool.Request(data=False)).message)
a = np.array([(t - t_start, p[0], p[3]) for t, p in js])
v = np.diff(a[:, 1]) / np.maximum(np.diff(a[:, 0]), 1e-3)
print(f'joint_states {len(a)} msgs in {a[-1,0]:.1f} s ({len(a)/a[-1,0]:.0f} Hz); pan range {np.degrees(a[:,1].min()):.1f}..{np.degrees(a[:,1].max()):.1f} deg')
mid = (a[1:, 0] > 0.8) & (a[1:, 0] < 1.8)
print(f'pan velocity during steady +0.3 rad/s: mean {v[mid].mean():.3f} std {v[mid].std():.3f} rad/s')
np.save('/tmp/servo_js.npy', a)
