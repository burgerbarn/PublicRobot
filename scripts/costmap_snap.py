"""Arm to drive pose (mind paused), wait for the standalone local costmap, render it."""
import sys, time, numpy as np, cv2, rclpy
sys.path.insert(0, '/home/burgerbarn/vision')
from mind_pause import mind_paused
from nav_msgs.msg import OccupancyGrid
from std_srvs.srv import Trigger
with mind_paused('costmap test'):
    rclpy.init(); n = rclpy.create_node('costmap_snap'); g = {}
    n.create_subscription(OccupancyGrid, '/costmap/costmap', lambda m: g.__setitem__('m', m), 2)
    c = n.create_client(Trigger, '/rosorin_board_driver/arm/drive'); c.wait_for_service(timeout_sec=5)
    f = c.call_async(Trigger.Request()); rclpy.spin_until_future_complete(n, f, timeout_sec=60); print('arm/drive', f.result().success)
    t = time.time()
    while time.time() - t < 8: rclpy.spin_once(n, timeout_sec=0.1)
    m = g['m']; W, H = m.info.width, m.info.height
    a = np.array(m.data, np.int16).reshape(H, W)
    img = np.full((H, W, 3), 60, np.uint8); img[a == 0] = (235, 235, 235); img[(a > 0) & (a < 99)] = (120, 170, 255); img[a >= 99] = (0, 0, 200)
    img = cv2.resize(np.flipud(img), (W * 8, H * 8), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite('/tmp/costmap.png', img)
    print('costmap', W, 'x', H, 'res', m.info.resolution, 'lethal cells', int((a >= 99).sum()), 'unknown', int((a < 0).sum()))
    n.destroy_node(); rclpy.shutdown()
