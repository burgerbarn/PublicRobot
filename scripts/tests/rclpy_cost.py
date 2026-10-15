"""What do the things a Python node does all day cost on THIS Jetson? CPU (share of one core) of: an idle node, timers,
publishing Odometry / Imu / String at the driver's rates, and subscribing to /scan as a message vs raw bytes.
Publishing variants run on ROS domain 77 (nothing listens); the /scan variants only listen on the live domain.
Usage: rclpy_cost.py <variant> [seconds]   variants: idle timer50 timer50_many odom50 imu55 state10 scan scan_raw all_pub"""
import os, resource, sys, time

V = sys.argv[1]; SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
if not V.startswith(('scan', 'image', 'odom_sub', 'imu_sub')):
    os.environ['ROS_DOMAIN_ID'] = '77'; os.environ['ROS_LOCALHOST_ONLY'] = '1'
import rclpy
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Imu, LaserScan
from std_msgs.msg import String

rclpy.init(); n = rclpy.create_node('rclpy_cost_' + V); cnt = [0]
def tick(): cnt[0] += 1
if V in ('timer50', 'timer50_many', 'timer50_mt'):
    n.create_timer(0.02, tick)
if V == 'timer50_many':                 # same timer in a node with as many idle entities as board_driver has
    keep = [n.create_subscription(String, f'bench/idle{i}', lambda m: None, 10) for i in range(12)]
    keep += [n.create_timer(3600.0, tick) for _ in range(4)]
    from std_srvs.srv import SetBool
    keep += [n.create_service(SetBool, f'bench/srv{i}', lambda q, r: r) for i in range(8)]
if V in ('odom50', 'all_pub'):
    p1 = n.create_publisher(Odometry, 'bench/odom', 10)
    def f1():
        m = Odometry(); m.header.stamp = n.get_clock().now().to_msg(); m.header.frame_id = 'odom'; m.child_frame_id = 'base_link'
        m.twist.twist.linear.x = 0.1; m.pose.covariance[0] = 0.01; m.twist.covariance[0] = 0.01
        p1.publish(m); tick()
    n.create_timer(0.02, f1)
if V in ('imu55', 'all_pub'):
    p2 = n.create_publisher(Imu, 'bench/imu', 10)
    def f2():
        m = Imu(); m.header.stamp = n.get_clock().now().to_msg(); m.linear_acceleration.z = 9.8
        p2.publish(m); tick()
    n.create_timer(1 / 55, f2)
if V in ('state10', 'all_pub'):
    p3 = n.create_publisher(String, 'bench/state', 10)
    n.create_timer(0.1, lambda: (p3.publish(String(data='x' * 600)), tick()))
if V in ('image', 'image_raw', 'image_poll20ms'):
    from sensor_msgs.msg import Image
    n.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: tick(), qos_profile_sensor_data, raw=V == 'image_raw')
if V == 'odom_sub':
    n.create_subscription(Odometry, '/wheel/odom', lambda m: tick(), 50)
if V == 'imu_sub':
    n.create_subscription(Imu, '/imu/data_raw', lambda m: tick(), 200)
if V == 'scan':
    n.create_subscription(LaserScan, '/scan', lambda m: tick(), qos_profile_sensor_data)
if V == 'scan_raw':
    n.create_subscription(LaserScan, '/scan', lambda m: tick(), qos_profile_sensor_data, raw=True)
# how the node is spun (SPIN=): once = rclpy.spin_once in a loop (adds and removes the node every call),
# exec = one executor kept, executor.spin_once in a loop, spin = executor.spin() in a thread
SPIN = os.environ.get('SPIN', 'once')
from rclpy.executors import SingleThreadedExecutor
ex = SingleThreadedExecutor(); ex.add_node(n)
if SPIN == 'spin':
    import threading
    threading.Thread(target=ex.spin, daemon=True).start()
def turn(timeout):
    if SPIN == 'once':
        rclpy.spin_once(n, timeout_sec=timeout)
    elif SPIN == 'exec':
        ex.spin_once(timeout_sec=timeout)
    else:
        time.sleep(timeout)
end = time.time() + 2
while time.time() < end:
    turn(0.1)
cnt[0] = 0; r0 = resource.getrusage(resource.RUSAGE_SELF); t0 = time.time()
while time.time() - t0 < SECS:
    turn(0.02 if V.endswith('poll20ms') else 0.1)
r = resource.getrusage(resource.RUSAGE_SELF); dt = time.time() - t0
c = r.ru_utime + r.ru_stime - r0.ru_utime - r0.ru_stime
print(f'{V:14s} spin={SPIN:5s} {cnt[0] / dt:6.1f} events/s  {100 * c / dt:5.1f} % of a core  {1e6 * c / max(cnt[0], 1):7.0f} us per event')
