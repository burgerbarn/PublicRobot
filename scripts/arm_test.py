"""Supervised arm tests for board_driver (robot on a stand, owner watching).
Usage: arm_test.py move <servo_id> <pulse> [<pulse> ...]   # sequence of goals, 2 s pause between
Always disables the arm at the end. Aborts if the driver's state isn't received.
"""
import json, sys, time
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

rclpy.init(); n = Node("arm_test"); st = {}
n.create_subscription(String, "rosorin_board_driver/state", lambda m: st.update(json.loads(m.data)), 10)
goal = n.create_publisher(JointState, "arm/goal", 10)
en = n.create_client(SetBool, "rosorin_board_driver/arm/enable")
rd = n.create_client(Trigger, "rosorin_board_driver/arm/read_state")

def spin(s):
    t = time.time()
    while time.time() - t < s: rclpy.spin_once(n, timeout_sec=0.02)

def call(c, r):
    c.wait_for_service(5); f = c.call_async(r); rclpy.spin_until_future_complete(n, f, timeout_sec=10); return f.result()

t0 = time.time()
while not st and time.time() - t0 < 10: spin(0.2)
if not st: print("ABORT: no driver state received - nothing sent"); sys.exit(2)
sid, pulses = sys.argv[2], [float(p) for p in sys.argv[3:]]
try:
    print("arm enable:", call(en, SetBool.Request(data=True)).message, flush=True)
    for p in pulses:
        m = JointState(); m.name = [sid]; m.position = [p]; goal.publish(m)
        print(f">> servo {sid} -> {p:.0f}", flush=True); spin(2.0 + abs(p - 500) / 25.0)
finally:
    print("arm disable:", call(en, SetBool.Request(data=False)).message, flush=True)
    r = call(rd, Trigger.Request())
    print("read-back:", {k: v["pos"] for k, v in json.loads(r.message).items()} if r.success else r.message, flush=True)
