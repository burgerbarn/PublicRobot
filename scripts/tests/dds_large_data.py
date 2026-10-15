"""How do camera-sized messages travel between processes on this robot, and what does it cost? Isolated domain 77:
one publisher of 640x400 RGB images (768 kB, 15 Hz, RELIABLE like the camera driver) and four subscribers (three
RELIABLE, one BEST_EFFORT, each doing a little work per frame like the robot's Python nodes). Prints the kernel's UDP
datagram and receive-buffer-overflow rates, loopback bytes, frames received and CPU. Run it with and without
FASTRTPS_DEFAULT_PROFILES_FILE to compare transports.   Usage: dds_large_data.py [seconds]"""
import os, subprocess, sys, time

os.environ['ROS_DOMAIN_ID'] = '77'
SECS = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].replace('.', '').isdigit() else 12.0
ROLE = sys.argv[2] if len(sys.argv) > 2 else 'main'
if ROLE == 'main':
    def udp():
        l = open('/proc/net/snmp').read().split('\n'); u = [x for x in l if x.startswith('Udp:')][1].split()
        lo = next(x for x in open('/proc/net/dev') if x.strip().startswith('lo:')).split()
        return int(u[1]), int(u[5]), int(lo[1])
    procs = [subprocess.Popen([sys.executable, __file__, str(SECS), 'pub'], stdout=subprocess.PIPE, text=True)]
    procs += [subprocess.Popen([sys.executable, __file__, str(SECS), f'sub{k}'], stdout=subprocess.PIPE, text=True) for k in range(4)]
    time.sleep(5.0)                                           # discovery + warm-up
    a = udp(); t0 = time.time(); time.sleep(SECS - 7.0); b = udp(); dt = time.time() - t0
    outs = [p.communicate(timeout=30)[0].strip() for p in procs]
    print(f'profile: {os.environ.get("FASTRTPS_DEFAULT_PROFILES_FILE", "(default transports)")}')
    print(f'UDP datagrams {(b[0] - a[0]) / dt:8.0f}/s   receive-buffer overflows {(b[1] - a[1]) / dt:7.0f}/s   loopback {(b[2] - a[2]) / dt / 1e6:6.1f} MB/s')
    for o in outs:
        print('  ' + o)
    sys.exit(0)
import resource
import rclpy
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image
rclpy.init(); n = rclpy.create_node('dds_large_' + ROLE); cnt = [0]
if ROLE == 'pub':
    pub = n.create_publisher(Image, 'bench/image', QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE))
    m = Image(); m.height, m.width, m.encoding, m.step = 400, 640, 'rgb8', 1920; m.data = bytes(768000)
    def tick():
        m.header.stamp = n.get_clock().now().to_msg(); pub.publish(m); cnt[0] += 1
    n.create_timer(1 / 15, tick)
else:
    def got(msg):
        cnt[0] += 1
        time.sleep(0.02)                                      # a Python node's own work per frame
    n.create_subscription(Image, 'bench/image', got, qos_profile_sensor_data if ROLE == 'sub3' else 2, raw=True)
r0 = resource.getrusage(resource.RUSAGE_SELF); t0 = time.time(); c0 = None
while time.time() - t0 < SECS:
    rclpy.spin_once(n, timeout_sec=0.05)
    if c0 is None and time.time() - t0 > 5.0:
        c0 = cnt[0]; r0 = resource.getrusage(resource.RUSAGE_SELF); t1 = time.time()
r = resource.getrusage(resource.RUSAGE_SELF); dt = time.time() - t1
print(f'{ROLE:5s} {(cnt[0] - (c0 or 0)) / dt:5.1f} frames/s   {100 * (r.ru_utime + r.ru_stime - r0.ru_utime - r0.ru_stime) / dt:5.1f} % of a core')
