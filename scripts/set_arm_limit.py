"""Set an arm limit on the running driver via its set_parameters service (no ros2 CLI/daemon).
Usage: set_arm_limit.py <servo_id> <lo> <hi>"""
import sys, time, rclpy
from rclpy.node import Node
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import SetParameters
sid, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
rclpy.init(); n = Node("set_arm_limit")
c = n.create_client(SetParameters, "rosorin_board_driver/set_parameters")
if not c.wait_for_service(timeout_sec=15): print("driver set_parameters service not found"); sys.exit(2)
p = Parameter(name=f"arm_limit_{sid}", value=ParameterValue(type=ParameterType.PARAMETER_INTEGER_ARRAY, integer_array_value=[lo, hi]))
f = c.call_async(SetParameters.Request(parameters=[p])); rclpy.spin_until_future_complete(n, f, timeout_sec=10)
r = f.result().results[0]; print(f"arm_limit_{sid} = [{lo}, {hi}]:", "OK" if r.successful else f"FAILED {r.reason}")
sys.exit(0 if r.successful else 1)
