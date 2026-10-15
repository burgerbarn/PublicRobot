"""Stand tests for board_driver. Usage: motion_test.py <step>"""
import json, sys, time, rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool, String
from std_srvs.srv import SetBool, Trigger

rclpy.init(); n = Node("motion_test"); st = {}
n.create_subscription(String, "rosorin_board_driver/state", lambda m: st.update(json.loads(m.data)), 10)
pub = n.create_publisher(Twist, "cmd_vel", 10)
est = n.create_publisher(Bool, "estop", 10)
en = n.create_client(SetBool, "rosorin_board_driver/enable")
rs = n.create_client(Trigger, "rosorin_board_driver/reset_estop")
def spin(s):
    t = time.time()
    while time.time() - t < s: rclpy.spin_once(n, timeout_sec=0.02)
def call(cli, req):
    cli.wait_for_service(5); f = cli.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=5); return f.result()
def drive(s, vx=0.0, vy=0.0, wz=0.0):
    m = Twist(); m.linear.x, m.linear.y, m.angular.z = vx, vy, wz; t = time.time()
    while time.time() - t < s: pub.publish(m); spin(0.1)
def show(tag): spin(0.6); ts = time.strftime("%H:%M:%S"); print(f"{ts} {tag}: {st}", flush=True)
def enable(on): r = call(en, SetBool.Request(data=on)); print(f"enable({on}) -> {r.success} {r.message}", flush=True)

step = sys.argv[1]; spin(1.5); show("start")
if not st: print("ABORT: no driver state received (DDS problem) - not sending anything"); sys.exit(2)
if step == "disabled":
    print(">> 3 s of forward 0.1 m/s while DISABLED (wheels must stay still)", flush=True); drive(3, vx=0.1); show("after")
elif step == "forward":
    enable(True); print(">> FORWARD 0.1 m/s for 3 s, then silence (deadman)", flush=True)
    drive(3, vx=0.1); print(">> silence", flush=True); show("after silence"); enable(False)
elif step == "rotate":
    enable(True); print(">> ROTATE +0.5 rad/s (counter-clockwise from above) for 3 s", flush=True)
    drive(3, wz=0.5); show("after"); enable(False)
elif step == "estop":
    enable(True); print(">> forward 0.1 m/s; e-stop topic at t=2 s", flush=True)
    drive(2, vx=0.1); est.publish(Bool(data=True)); print(">> ESTOP published", flush=True); drive(2, vx=0.1); show("latched")
    enable(True); r = call(rs, Trigger.Request()); print("reset:", r.message, flush=True); show("after reset")
elif step == "gamepad":
    enable(True); print(">> forward 0.1 m/s for 15 s -- PRESS ANY GAMEPAD BUTTON", flush=True)
    drive(15, vx=0.1); show("after"); r = call(rs, Trigger.Request()); print("reset:", r.message, flush=True)

# always leave the robot disabled
enable(False); show("end")
