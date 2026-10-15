"""First MoveIt-planned moves: named states via move_group's MoveGroup action (plan + execute through board_driver)."""
import json, sys, time, rclpy
from rclpy.action import ActionClient
from moveit_msgs.action import MoveGroup
from moveit_msgs.msg import Constraints, JointConstraint, MotionPlanRequest
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
NAMED = {'rest': [0.0, -1.50796, 1.9059, 1.34879, 0.0], 'drive': [0.0, -1.22732, 1.88077, 1.42419, 0.0]}
rclpy.init(); n = rclpy.create_node('moveit_try'); st = {}
n.create_subscription(String, '/rosorin_board_driver/state', lambda m: st.update(json.loads(m.data)), 5)
def call(srv, T, req):
    c = n.create_client(T, f'/rosorin_board_driver/{srv}'); c.wait_for_service(timeout_sec=5)
    f = c.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=60); return f.result()
t = time.time()
while not st and time.time() - t < 5: rclpy.spin_once(n, timeout_sec=0.1)
print('state:', {k: st.get(k) for k in ('enabled', 'estop', 'arm_enabled', 'arm_holding', 'arm_pose')})
if st.get('enabled') or st.get('estop'): sys.exit('refusing: wheels enabled or e-stop')
if not st.get('arm_holding'):
    r = call('arm/recover', Trigger, Trigger.Request()); print('recover:', r.success, r.message[:80])
r = call('arm/enable', SetBool, SetBool.Request(data=True)); print('enable:', r.success, r.message[:80])
ac = ActionClient(n, MoveGroup, '/move_action'); ac.wait_for_server(timeout_sec=10)
def go(name):
    req = MotionPlanRequest(group_name='arm', num_planning_attempts=3, allowed_planning_time=5.0,
                            max_velocity_scaling_factor=0.5, max_acceleration_scaling_factor=0.5)
    req.goal_constraints = [Constraints(joint_constraints=[JointConstraint(joint_name=f'joint{i+1}', position=v,
                            tolerance_above=0.01, tolerance_below=0.01, weight=1.0) for i, v in enumerate(NAMED[name])])]
    g = MoveGroup.Goal(request=req); g.planning_options.plan_only = False
    t0 = time.time(); f = ac.send_goal_async(g); rclpy.spin_until_future_complete(n, f, timeout_sec=10)
    h = f.result(); rf = h.get_result_async(); rclpy.spin_until_future_complete(n, rf, timeout_sec=60)
    code = rf.result().result.error_code.val
    print(f'{name}: MoveIt error_code {code} ({"SUCCESS" if code == 1 else "FAIL"}) in {time.time()-t0:.1f} s, pose {st.get("arm_pose")}')
    return code == 1
try:
    ok = go('drive') and go('rest')
finally:
    print('disable:', call('arm/enable', SetBool, SetBool.Request(data=False)).message)
