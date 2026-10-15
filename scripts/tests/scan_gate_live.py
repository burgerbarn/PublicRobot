"""Live check of the LiDAR-loss path with the wheels DISABLED (nothing moves): kill the LiDAR reader process, watch
the base driver's own view of the scan (state.scan_age) and the reader coming back (launch respawn).
Pass = the driver saw the scan go stale beyond its scan_timeout, and scans returned without anybody's help.
The wheel-stop itself (enabled + commanded + scan lost -> 'no scan' -> zero) is unit-tested in test_motion.py and
is a case in scripts/stop_proof.py for the next run on the stand."""
import json, os, signal, subprocess, sys, time

import rclpy
from std_msgs.msg import String

rclpy.init(); n = rclpy.create_node('scan_gate_live'); st = []
n.create_subscription(String, '/rosorin_board_driver/state', lambda m: st.append((time.time(), json.loads(m.data))), 20)


def spin(s):
    end = time.time() + s
    while time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.05)


spin(2.0)
if not st or st[-1][1].get('enabled'):
    print('abort: no driver state, or wheels enabled (this check is for a disabled robot)'); sys.exit(2)
before = st[-1][1].get('scan_age')
pids = subprocess.run(['pgrep', '-f', 'lib/rosorin_base/[l]idar_reader'], capture_output=True, text=True).stdout.split()
if not pids:
    print('abort: LiDAR reader process not found'); sys.exit(2)
t0 = time.time()
for p in pids:
    os.kill(int(p), signal.SIGKILL)
spin(9.0)
ages = [(round(t - t0, 2), s.get('scan_age')) for t, s in st if t >= t0]
worst = max((a for _, a in ages if a is not None), default=None)
back = next((t for t, a in ages if t > 1.0 and a is not None and a < 0.3), None)
new = subprocess.run(['pgrep', '-f', 'lib/rosorin_base/[l]idar_reader'], capture_output=True, text=True).stdout.split()
print(json.dumps(dict(scan_age_before=before, killed_pids=pids, worst_scan_age_s=worst, scans_back_after_s=back,
                      reader_pids_now=new, state_blocked=st[-1][1].get('blocked'))))
ok = worst is not None and worst > 0.5 and back is not None and new and new != pids
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
