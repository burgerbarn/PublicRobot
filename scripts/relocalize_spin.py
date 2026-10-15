"""AMCL global relocalization: reinitialize particles over the map, spin 360 deg in place via Nav2's Spin behavior
(-> cmd_vel_nav -> collision_monitor), motion enabled only for the spin and ALWAYS disabled after."""
import math, time, rclpy
from rclpy.action import ActionClient
from nav2_msgs.action import Spin
from std_srvs.srv import Empty, SetBool
rclpy.init(); n = rclpy.create_node('relocalize_spin')
def call(name, T, req):
    c = n.create_client(T, name); c.wait_for_service(timeout_sec=5); f = c.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=10); return f.result()
print('global localization:', call('/reinitialize_global_localization', Empty, Empty.Request()) is not None)
print('enable:', call('/rosorin_board_driver/enable', SetBool, SetBool.Request(data=True)).message)
try:
    ac = ActionClient(n, Spin, 'spin'); ac.wait_for_server(timeout_sec=10)
    g = Spin.Goal(); g.target_yaw = 2 * math.pi; g.time_allowance.sec = 40
    f = ac.send_goal_async(g); rclpy.spin_until_future_complete(n, f, timeout_sec=10)
    r = f.result().get_result_async(); rclpy.spin_until_future_complete(n, r, timeout_sec=60)
    print('spin result status:', r.result().status)
finally:
    print('disable:', call('/rosorin_board_driver/enable', SetBool, SetBool.Request(data=False)).message)
