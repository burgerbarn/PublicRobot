"""Save one frame of /aurora/rgb/image_raw (and depth) to ~/vision/frame_rgb.png / frame_depth.npy."""
import numpy as np, rclpy, cv2
from rclpy.node import Node
from sensor_msgs.msg import Image
got = {}
class G(Node):
    def __init__(self):
        super().__init__('grab')
        for k, t in (('rgb', '/aurora/rgb/image_raw'), ('depth', '/aurora/depth/image_raw')):
            self.create_subscription(Image, t, lambda m, k=k: got.setdefault(k, m), 5)
rclpy.init(); n = G()
while len(got) < 2: rclpy.spin_once(n, timeout_sec=1)
m = got['rgb']; print('rgb', m.width, m.height, m.encoding, m.header.frame_id)
a = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)
cv2.imwrite('frame_rgb.png', cv2.cvtColor(a, cv2.COLOR_RGB2BGR) if m.encoding == 'rgb8' else a)
d = got['depth']; print('depth', d.width, d.height, d.encoding, d.header.frame_id)
np.save('frame_depth.npy', np.frombuffer(d.data, np.uint16).reshape(d.height, d.width))
