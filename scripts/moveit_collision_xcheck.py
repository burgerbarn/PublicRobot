import sys, time, numpy as np, rclpy
sys.path.insert(0, '/home/burgerbarn/model_check')
import arm_model as am
from moveit_msgs.srv import GetStateValidity
from moveit_msgs.msg import RobotState
from sensor_msgs.msg import JointState
M = am.ArmModel('/home/burgerbarn/model_check/robot_model.json'); K = 0.0041887902047863905
lim = {1: (34, 966), 2: (95, 941), 3: (40, 881), 4: (121, 935), 5: (500, 500)}   # j5 fixed: known false positive
rclpy.init(); n = rclpy.create_node('xcheck'); c = n.create_client(GetStateValidity, '/check_state_validity'); c.wait_for_service(timeout_sec=10)
rng = np.random.default_rng(1); agree = 0; mv_only = []; md_only = []; N = int(sys.argv[1])
for i in range(N):
    p = {j: int(rng.integers(lo, hi + 1)) for j, (lo, hi) in lim.items()}; p[10] = 499
    model_hit = bool(am.collisions(M, p))
    req = GetStateValidity.Request(); req.group_name = 'arm'
    req.robot_state = RobotState(joint_state=JointState(name=[f'joint{j}' for j in range(1, 6)], position=[(500 - p[j]) * K for j in range(1, 6)]))
    f = c.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=5)
    mv_hit = not f.result().valid
    if mv_hit == model_hit: agree += 1
    elif mv_hit: mv_only.append(p)
    else: md_only.append((p, am.collisions(M, p)))
print(f'{N} poses: agree {agree} ({100*agree/N:.1f}%), MoveIt-only collisions {len(mv_only)}, model-only collisions {len(md_only)}')
for p, h in md_only[:5]: print('model-only', p, h)
for p in mv_only[:3]: print('moveit-only', p)
