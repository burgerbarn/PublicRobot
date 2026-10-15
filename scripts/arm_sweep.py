"""Gamepad-bounded limit sweep for one arm joint (robot on a stand, owner watching).
Usage: arm_sweep.py <servo_id> <rest_pulse> <target_pulse> <safe_lo> <safe_hi>
Moves the joint slowly (25 p/s) toward target; the owner presses any gamepad button when it gets
close to anything. Records the stop position, returns to rest, disables the arm and restores the
joint's safe limits. Results appended to ~/setup/arm_sweeps.json.
"""
import json, os, sys, time
import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
from sensor_msgs.msg import JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

sid, rest, target, safe_lo, safe_hi = sys.argv[1], *map(int, sys.argv[2:6])
rclpy.init(); n = Node("arm_sweep"); st = {}
n.create_subscription(String, "rosorin_board_driver/state", lambda m: st.update(json.loads(m.data)), 10)
goal = n.create_publisher(JointState, "arm/goal", 10)
D = "rosorin_board_driver"
en = n.create_client(SetBool, f"{D}/arm/enable"); rd = n.create_client(Trigger, f"{D}/arm/read_state")
rs = n.create_client(Trigger, f"{D}/reset_estop"); sp = n.create_client(SetParameters, f"{D}/set_parameters")

def spin(s):
    t = time.time()
    while time.time() - t < s: rclpy.spin_once(n, timeout_sec=0.02)
def call(c, r):
    c.wait_for_service(5); f = c.call_async(r); rclpy.spin_until_future_complete(n, f, timeout_sec=10); return f.result()
def setp(**kw):
    ps = []
    for k, v in kw.items():
        if isinstance(v, list):
            pv = ParameterValue(type=ParameterType.PARAMETER_INTEGER_ARRAY, integer_array_value=v)
        else:
            pv = ParameterValue(type=ParameterType.PARAMETER_DOUBLE, double_value=float(v))
        ps.append(Parameter(name=k, value=pv))
    r = call(sp, SetParameters.Request(parameters=ps))
    ok = all(x.successful for x in r.results)
    if not ok: raise RuntimeError(f"param set failed: {[x.reason for x in r.results]}")
def pos():
    r = call(rd, Trigger.Request()); return json.loads(r.message)[sid]["pos"] if r.success else None
def go(p, timeout):
    m = JointState(); m.name = [sid]; m.position = [float(p)]; goal.publish(m)
    t = time.time(); spin(1.0); last = 0
    while time.time() - t < timeout:
        spin(0.1)
        if st.get("estop"): return "estop"
        if not st.get("arm_moving"): return "done"
        if time.time() - last > 2: print(f"   pose {st.get('arm_pose', {}).get(sid)}", flush=True); last = time.time()
    return "timeout"

t0 = time.time()
while not st and time.time() - t0 < 10: spin(0.2)
if not st: print("ABORT: no driver state received - nothing sent"); sys.exit(2)
result = None
try:
    call(en, SetBool.Request(data=False))
    setp(arm_max_speed=25.0, **{f"arm_limit_{sid}": [0, 1000]})
    print("arm enable:", call(en, SetBool.Request(data=True)).message, flush=True)
    print(f">> SWEEP servo {sid} -> {target} at 25 p/s. PRESS A GAMEPAD BUTTON when it gets close to anything.", flush=True)
    how = go(target, 1.875 * abs(target - rest) / 25 + 10)
    call(en, SetBool.Request(data=False))
    stop = pos()
    result = {"servo": int(sid), "target": target, "stopped_by": how, "stop_pos": stop, "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print("RESULT:", result, flush=True)
    if st.get("estop"): print("reset:", call(rs, Trigger.Request()).message, flush=True)
    setp(arm_max_speed=50.0)
    print("arm enable:", call(en, SetBool.Request(data=True)).message, flush=True)
    print(f">> return to rest {rest}", flush=True); print("return:", go(rest, 20), flush=True)
finally:
    call(en, SetBool.Request(data=False))
    try:
        setp(arm_max_speed=50.0, **{f"arm_limit_{sid}": [safe_lo, safe_hi]})
        print(f"limits restored to [{safe_lo}, {safe_hi}]", flush=True)
    except Exception as e:
        print("WARNING could not restore limits:", e, flush=True)
    print("final pos:", pos(), "| arm disabled", flush=True)
    if result:
        f = os.path.expanduser("~/setup/arm_sweeps.json")
        data = json.load(open(f)) if os.path.exists(f) else []
        data.append(result); json.dump(data, open(f, "w"), indent=1)
