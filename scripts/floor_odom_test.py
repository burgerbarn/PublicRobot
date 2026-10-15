"""Floor test: odometry vs LiDAR scan matching. Moves: forward 0.3 m, strafe left 0.3 m, rotate +90 deg.
Each move: stationary scans before/after -> ICP gives true motion in the start base frame; compared with
wheel/odom and EKF deltas. Refuses a move if the LiDAR sector in that direction is closer than CLEAR m."""
import json, math, sys, time
import numpy as np
from scipy.spatial import cKDTree
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from std_srvs.srv import SetBool

LASER_X, LASER_YAW = 0.048156, math.pi       # base_link -> laser (launch static TF)
CLEAR = 0.6
rclpy.init(); n = Node("floor_odom_test"); st = {}; od = {}; scans = []
n.create_subscription(String, "rosorin_board_driver/state", lambda m: st.update(json.loads(m.data)), 10)
def keep(k):
    def cb(m):
        p = m.pose.pose; q = p.orientation
        od[k] = (p.position.x, p.position.y, 2 * math.atan2(q.z, q.w))
    return cb
n.create_subscription(Odometry, "wheel/odom", keep("wheel"), 10)
n.create_subscription(Odometry, "odometry/filtered", keep("ekf"), 10)
n.create_subscription(LaserScan, "scan", scans.append, 10)
pub = n.create_publisher(Twist, "cmd_vel", 10)
en = n.create_client(SetBool, "rosorin_board_driver/enable")
def spin(s):
    t = time.time()
    while time.time() - t < s: rclpy.spin_once(n, timeout_sec=0.02)
def call(cli, req):
    cli.wait_for_service(5); f = cli.call_async(req); rclpy.spin_until_future_complete(n, f, timeout_sec=5); return f.result()
def drive(s, vx=0.0, vy=0.0, wz=0.0):
    m = Twist(); m.linear.x, m.linear.y, m.angular.z = vx, vy, wz; t = time.time()
    while time.time() - t < s: pub.publish(m); spin(0.05)
def enable(on): r = call(en, SetBool.Request(data=on)); print(f"enable({on}) -> {r.success} {r.message}", flush=True)

def points(secs=1.5):
    """Median range per beam over `secs` of scans -> Nx2 points in base_link."""
    del scans[:]; spin(secs)
    m = scans[-1]; R = np.array([s.ranges for s in scans], dtype=float)
    R[(R < m.range_min) | (R > m.range_max) | ~np.isfinite(R)] = np.nan
    r = np.nanmedian(R, axis=0)
    a = m.angle_min + np.arange(len(r)) * m.angle_increment + LASER_YAW
    ok = np.isfinite(r)
    return np.c_[LASER_X + r[ok] * np.cos(a[ok]), r[ok] * np.sin(a[ok])]

def sector_min(P, center, half=math.radians(25)):
    ang = np.arctan2(P[:, 1], P[:, 0]); d = np.abs(np.arctan2(np.sin(ang - center), np.cos(ang - center)))
    sel = np.hypot(P[:, 0], P[:, 1])[d <= half]
    return float(sel.min()) if len(sel) else float("inf")

def icp(src, dst, guess, iters=60):
    """Find (x, y, th): dst ~= R(th) src + t. src = after-scan, dst = before-scan."""
    tree = cKDTree(dst); x, y, th = guess
    for i in range(iters):
        c, s = math.cos(th), math.sin(th)
        S = src @ np.array([[c, s], [-s, c]]) + [x, y]
        d, j = tree.query(S)
        gate = max(0.05, np.percentile(d, 80)) if i > 10 else 0.5
        k = d < gate
        A, B = S[k], dst[j[k]]
        ma, mb = A.mean(0), B.mean(0)
        H = (A - ma).T @ (B - mb); U, _, Vt = np.linalg.svd(H); Rm = Vt.T @ U.T
        if np.linalg.det(Rm) < 0: Vt[1] *= -1; Rm = Vt.T @ U.T
        dth = math.atan2(Rm[1, 0], Rm[0, 0]); tt = mb - Rm @ ma
        c2, s2 = math.cos(dth), math.sin(dth)
        x, y = c2 * x - s2 * y + tt[0], s2 * x + c2 * y + tt[1]; th += dth
    return (x, y, th), float(np.median(d[k])), int(k.sum())

def rel(a, b):
    """pose b expressed in pose a's frame."""
    dx, dy = b[0] - a[0], b[1] - a[1]; c, s = math.cos(-a[2]), math.sin(-a[2])
    return (c * dx - s * dy, s * dx + c * dy, math.atan2(math.sin(b[2] - a[2]), math.cos(b[2] - a[2])))
def fmt(p): return "x %+.3f  y %+.3f  yaw %+6.1f deg" % (p[0], p[1], math.degrees(p[2]))

t0 = time.time()
while (not st or len(od) < 2 or not scans) and time.time() - t0 < 10: spin(0.2)
if not st or len(od) < 2 or not scans: print("ABORT: missing inputs (DDS)"); sys.exit(2)
moves = [("forward 0.1 m/s x 3 s", dict(vx=0.1), 0.0),
         ("strafe left 0.1 m/s x 3 s", dict(vy=0.1), math.pi / 2),
         ("rotate +0.5 rad/s x 3.2 s", dict(wz=0.5), None),
         ("rotate -0.5 rad/s x 3.2 s (right)", dict(wz=-0.5), None),
         ("strafe right 0.1 m/s x 3 s", dict(vy=-0.1), -math.pi / 2)]
sel = sys.argv[1:] or ["0", "1", "2"]
if sel == ["static"]:                        # no motion: ICP of two stationary captures should be ~0
    P0 = points(); P1 = points()
    print("front/left/right clearance %.2f %.2f %.2f m" % (sector_min(P0, 0), sector_min(P0, math.pi / 2),
                                                          sector_min(P0, -math.pi / 2)))
    (ix, iy, ith), res, npts = icp(P1, P0, (0.0, 0.0, 0.0)); print("static", fmt((ix, iy, ith)), res, npts)
    icp_t = icp(P1, P0, (0.05, -0.03, 0.1)); print("static from offset guess", fmt(icp_t[0]))
    sys.exit(0)
try:
    for idx in map(int, sel):
        name, kw, direction = moves[idx]
        spin(1.0); P0 = points(); o0 = dict(od)
        if direction is not None and sector_min(P0, direction) < CLEAR:
            print(f"SKIP {name}: obstacle {sector_min(P0, direction):.2f} m < {CLEAR} m"); continue
        enable(True); print(">>", name, flush=True)
        drive(3.2 if "rotate" in name else 3.0, **kw); enable(False); spin(1.0)
        P1 = points(); o1 = dict(od)
        w, e = rel(o0["wheel"], o1["wheel"]), rel(o0["ekf"], o1["ekf"])
        np.savez(f"/home/burgerbarn/floor_test_{time.strftime('%H%M%S')}_{idx}.npz", P0=P0, P1=P1,
                 wheel=w, ekf=e)
        print(f"   wheel {fmt(w)}\n   ekf   {fmt(e)}", flush=True)
        if "rotate" in name:                  # in-place rotation: scan shifts in angle; h1[a] = h0[a + th]
            def prof(P):
                ang = np.degrees(np.arctan2(P[:, 1], P[:, 0])).astype(int) % 360; r = np.hypot(P[:, 0], P[:, 1])
                h = np.full(360, np.inf); np.minimum.at(h, ang, r); h[np.isinf(h)] = np.nan; return h
            h0, h1 = prof(P0), prof(P1); best = []
            for sft in range(360):
                x = np.roll(h0, -sft); k = ~np.isnan(x) & ~np.isnan(h1)
                if k.sum() > 60: best.append((float(np.median(np.abs(x[k] - h1[k]))), (sft + 180) % 360 - 180))
            best.sort()
            print("   lidar angular shift: " + ", ".join(f"{s:+d} deg ({r*1000:.0f} mm)" for r, s in best[:3]),
                  "  [same sign as gyro = scan handedness correct]", flush=True)
        # try both scan handedness hypotheses, several yaw guesses each; lowest residual wins
        for tag, M in (("as-is   ", np.diag([1, 1])), ("mirror-y", np.diag([1, -1]))):
            best = min((icp(P1 @ M, P0 @ M, (0.0, 0.0, g)) for g in np.radians([-120, -90, -45, 0, 45, 90, 120])),
                       key=lambda r: r[1])
            print(f"   lidar {tag} {fmt(best[0])}   (icp resid {best[1]*1000:.0f} mm, {best[2]} pts)", flush=True)
finally:
    enable(False)
