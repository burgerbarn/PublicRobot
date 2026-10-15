#!/bin/bash
# Runs as burgerbarn in a container on the fakelidar home volume, fake board + fake LiDAR already up.
# The base is started the way rosorin-base.service starts it (its ExecStart after ch16's sed), in the background,
# instead of `sudo systemctl restart rosorin-base` (no systemd in the container). Then the read parts of the
# systemd-blocked blocks of ch14 (Step 7, Step 9), ch16 (EKF, rf2o) and ch17 (first look) run as printed.
export ROS_DOMAIN_ID=0
echo "### base start (unit ExecStart, ch16 form)"
grep -o "ExecStart=.*" ~/ros2_ws/src/rosorin_base/systemd/rosorin-base.service
setsid bash -c 'source /opt/ros/humble/setup.bash && source /home/burgerbarn/ext_ws/install/setup.bash && source /home/burgerbarn/ros2_ws/install/setup.bash && exec ros2 launch rosorin_base base.launch.py' > /tmp/base.log 2>&1 < /dev/null &
BASE=$!
sleep 15

echo "### ch14 Step 7 (read part)"
source /opt/ros/humble/setup.bash
timeout 8 ros2 topic hz /scan 2>/dev/null | tail -1
grep -i lidar /tmp/base.log | grep -v "process started" | tail -3

echo "### ch14 Step 9: the USB device disappears (unbind/bind line -> SIGUSR1 to the fake LiDAR: 3 s drop)"
sudo pkill -USR1 -f "^python3 /opt/fakelidar/fake_lidar.py" 2>/dev/null; sleep 3; sleep 6
ls -l /dev/lidar
grep lidar_reader /tmp/base.log | grep -E "reopen" | tail -4
timeout 6 ros2 topic hz /scan 2>&1 | grep -m1 "average rate"

echo "### ch14 Step 9: scan_gate_live.py"
source /opt/ros/humble/setup.bash && source ~/ros2_ws/install/setup.bash
python3 ~/setup/tests/scan_gate_live.py 2>&1 | tail -4
grep -iE "lidar|respawn|process has died" /tmp/base.log | grep -E "died|started|reading" | tail -5

echo "### ch16 EKF block (read part)"
source ~/ros2_ws/install/setup.bash
timeout 8 ros2 topic hz /odometry/filtered 2>/dev/null | tail -2
timeout 5 ros2 topic echo --once /odometry/filtered 2>&1 | head -12
timeout 6 ros2 run tf2_ros tf2_echo odom base_link 2>&1 | grep -A2 Translation | head -3
grep -m1 "ekf_node.*process started" /tmp/base.log

echo "### ch16 rf2o block (read part)"
source ~/ext_ws/install/setup.bash; source ~/ros2_ws/install/setup.bash
ros2 topic info /odom_rf2o
timeout 6 ros2 topic hz /odom_lidar 2>&1 | grep -m1 "average rate"

echo "### extra: /scan vs /scan_rf2o (self-returns), /odom_lidar standing still"
timeout 6 ros2 topic hz /scan_rf2o 2>&1 | grep -m1 "average rate"
python3 - <<'PY'
import math, time, rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
rclpy.init(); n = Node('fakelidar_probe'); got = {}; od = []
for t in ('scan', 'scan_rf2o'):
    n.create_subscription(LaserScan, t, lambda m, t=t: got.setdefault(t, m), 10)
n.create_subscription(Odometry, 'odom_lidar', lambda m: od.append(m), 50)
t0 = time.time()
while time.time() - t0 < 5: rclpy.spin_once(n, timeout_sec=0.1)
s, r = got['scan'], got['scan_rf2o']
low = [i for i, x in enumerate(s.ranges) if x < s.range_min]
print('scan: finite', sum(map(math.isfinite, s.ranges)), 'below range_min', len(low),
      'at bearings (deg, base_link) %.0f-%.0f and %.0f-%.0f' % (
          min((180 + i * 0.9) % 360 for i in low if (180 + i * 0.9) % 360 < 180),
          max((180 + i * 0.9) % 360 for i in low if (180 + i * 0.9) % 360 < 180),
          min((180 + i * 0.9) % 360 for i in low if (180 + i * 0.9) % 360 >= 180),
          max((180 + i * 0.9) % 360 for i in low if (180 + i * 0.9) % 360 >= 180)),
      'range %.3f-%.3f m' % (min(s.ranges[i] for i in low), max(s.ranges[i] for i in low)))
print('scan_rf2o: finite', sum(map(math.isfinite, r.ranges)), 'below range_min', sum(1 for x in r.ranges if x < r.range_min))
if od:
    print('odom_lidar msgs in 5 s', len(od), 'max |vx| %.4f max |vy| %.4f max |wz| %.4f' % (
        max(abs(m.twist.twist.linear.x) for m in od), max(abs(m.twist.twist.linear.y) for m in od),
        max(abs(m.twist.twist.angular.z) for m in od)))
PY
grep -cE "Eigensolver couldn't find a solution" /tmp/base.log | sed 's/^/rf2o Eigensolver warnings so far: /'

echo "### ch17 first look: slam_toolbox 40 s, second session"
source /opt/ros/humble/setup.bash; source ~/ros2_ws/install/setup.bash
( timeout 40 ros2 launch rosorin_base slam.launch.py > /tmp/slam.log 2>&1 & )
sleep 15
source /opt/ros/humble/setup.bash
timeout 8 ros2 topic echo --once /map --field info
timeout 6 ros2 run tf2_ros tf2_echo map base_link 2>&1 | grep -m2 -E "Translation|RPY \(deg"
python3 - <<'PY'
import rclpy, time
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from nav_msgs.msg import OccupancyGrid
rclpy.init(); n = Node('map_count'); got = []
q = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
n.create_subscription(OccupancyGrid, 'map', lambda m: got.append(m), q)
t = time.time()
while not got and time.time() - t < 8: rclpy.spin_once(n, timeout_sec=0.2)
if got:
    d = got[-1].data
    print('map %dx%d res %.2f: occupied %d free %d unknown %d' % (got[-1].info.width, got[-1].info.height,
          got[-1].info.resolution, sum(1 for v in d if v > 50), sum(1 for v in d if v == 0), sum(1 for v in d if v < 0)))
PY
sleep 26
grep -E "ERROR|WARN" /tmp/slam.log | head -5

echo "### stop the base (SIGINT to its process group = Ctrl-C)"
kill -INT -- -$BASE; sleep 6
pgrep -af "lib/rosorin_base|static_transform_publisher|rf2o" || echo "all stopped"
