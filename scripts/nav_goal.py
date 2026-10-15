"""Enable motion, send one NavigateToPose goal (map frame), report, ALWAYS disable after.
Usage: nav_goal.py x y yaw_deg [timeout_s]"""
import math, sys, time
import rclpy
from rclpy.action import ActionClient
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from std_srvs.srv import SetBool

x, y, yaw = float(sys.argv[1]), float(sys.argv[2]), math.radians(float(sys.argv[3]))
timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 60.0
rclpy.init(); n = rclpy.create_node('nav_goal'); pose = {}
n.create_subscription(PoseWithCovarianceStamped, 'amcl_pose',
                      lambda m: pose.update(p=(m.pose.pose.position.x, m.pose.pose.position.y,
                                               2 * math.atan2(m.pose.pose.orientation.z, m.pose.pose.orientation.w))), 10)
en = n.create_client(SetBool, 'rosorin_board_driver/enable')
def enable(on):
    en.wait_for_service(5); f = en.call_async(SetBool.Request(data=on)); rclpy.spin_until_future_complete(n, f, timeout_sec=5)
    print(f'enable({on}) -> {f.result().message if f.result() else "NO REPLY"}', flush=True)
ac = ActionClient(n, NavigateToPose, 'navigate_to_pose')
try:
    if not ac.wait_for_server(timeout_sec=10): raise SystemExit('ABORT: navigate_to_pose server not available')
    g = NavigateToPose.Goal(); g.pose.header.frame_id = 'map'
    g.pose.pose.position.x, g.pose.pose.position.y = x, y
    g.pose.pose.orientation.z, g.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
    enable(True)
    last = [0.0]
    def fb(m):
        f = m.feedback
        if time.time() - last[0] > 1.0:
            last[0] = time.time()
            print(f'  remaining {f.distance_remaining:.2f} m, recoveries {f.number_of_recoveries}', flush=True)
    sf = ac.send_goal_async(g, feedback_callback=fb); rclpy.spin_until_future_complete(n, sf, timeout_sec=10)
    gh = sf.result()
    if not gh or not gh.accepted: raise SystemExit('goal rejected')
    t0 = time.time(); rf = gh.get_result_async()
    while not rf.done() and time.time() - t0 < timeout: rclpy.spin_once(n, timeout_sec=0.1)
    if not rf.done():
        print('TIMEOUT: cancelling'); cf = gh.cancel_goal_async(); rclpy.spin_until_future_complete(n, cf, timeout_sec=5)
    else:
        st = {4: 'SUCCEEDED', 5: 'CANCELED', 6: 'ABORTED'}.get(rf.result().status, rf.result().status)
        print(f'result: {st} after {time.time() - t0:.1f} s', flush=True)
finally:
    enable(False)
    t = time.time()
    while time.time() - t < 1.5: rclpy.spin_once(n, timeout_sec=0.1)
    if 'p' in pose:
        px, py, pth = pose['p']
        print(f'amcl pose: x {px:+.3f} y {py:+.3f} yaw {math.degrees(pth):+.1f} deg  (goal {x:+.3f} {y:+.3f} {math.degrees(yaw):+.1f})')
