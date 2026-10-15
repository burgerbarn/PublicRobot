"""Stand test for wheel/odom + EKF: 3 s forward, 3 s strafe left, 3 s rotate; prints deltas.
On the stand wheels spin free: expect x/y from commanded velocity, yaw ~0 (gyro sees no turn)."""
import json, math, sys, time, rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from std_msgs.msg import String
from std_srvs.srv import SetBool

rclpy.init(); n = Node("odom_test"); st = {}; od = {}
n.create_subscription(String, "rosorin_board_driver/state", lambda m: st.update(json.loads(m.data)), 10)
def keep(k):
    def cb(m):
        p = m.pose.pose; q = p.orientation
        od[k] = (p.position.x, p.position.y, math.degrees(2 * math.atan2(q.z, q.w)))
    return cb
n.create_subscription(Odometry, "wheel/odom", keep("wheel"), 10)
n.create_subscription(Odometry, "odometry/filtered", keep("ekf"), 10)
pub = n.create_publisher(Twist, "cmd_vel", 10)
en = n.create_client(SetBool, "rosorin_board_driver/enable")
def spin(s):
    t = time.time()
    while time.time() - t < s: rclpy.spin_once(n, timeout_sec=0.02)
def call(cli, req):
    cli.wait_for_service(5); f = cli.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=5); return f.result()
def drive(s, vx=0.0, vy=0.0, wz=0.0):
    m = Twist(); m.linear.x, m.linear.y, m.angular.z = vx, vy, wz; t = time.time()
    while time.time() - t < s: pub.publish(m); spin(0.05)
def enable(on): r = call(en, SetBool.Request(data=on)); print(f"enable({on}) -> {r.success} {r.message}", flush=True)
def fmt(p): return "x %+.3f y %+.3f yaw %+.2f deg" % p

t0 = time.time()
while (not st or len(od) < 2) and time.time() - t0 < 10: spin(0.2)   # DDS discovery delay
if not st or len(od) < 2: print("ABORT: missing state/odom (DDS)", st.keys(), od.keys()); sys.exit(2)
try:
    for name, kw in (("forward 0.1 m/s", dict(vx=0.1)), ("strafe left 0.1 m/s", dict(vy=0.1)),
                     ("rotate +0.5 rad/s", dict(wz=0.5))):
        spin(1.0); a = dict(od)
        enable(True); print(">>", name, "3 s", flush=True); drive(3, **kw); enable(False); spin(1.5)
        for k in ("wheel", "ekf"):
            d = tuple(b - c for b, c in zip(od[k], a[k]))
            print(f"   {k:5s} delta {fmt(d)}", flush=True)
finally:
    enable(False)
