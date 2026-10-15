import sys, subprocess
import numpy as np
from arm_model import ArmModel
m = ArmModel("robot_model.json")
pulses = {1: 500, 2: 722, 3: 44, 4: 149, 5: 501}
if len(sys.argv) > 1: pulses.update({int(k): int(v) for k, v in (a.split("=") for a in sys.argv[1].split(","))})
q = m.q_from_pulses(pulses); F = m.link_frames(q); C = m.box_corners(F)
W, H, s = 800, 600, 1500.0          # px, px, px per metre; view: x right, z up (side view)
ox, oz = 330, 520
img = np.full((H, W, 3), 255, np.uint8)
def px(x, z): return int(ox + x * s), int(oz - z * s)
h = m.m["chassis_heightmap"]; Z = np.array(h["z"]); prof = Z.max(1)      # max over y
for i, zt in enumerate(prof):
    if zt < 0: continue
    x0 = h["origin_xy"][0] + i * h["cell"]; a, b = px(x0, zt); c, d = px(x0 + h["cell"], 0)
    img[max(b,0):d, max(a,0):c] = (190, 190, 190)
cols = {"link1": (220, 0, 0), "link2": (0, 150, 0), "link3": (0, 0, 220), "link4": (200, 120, 0), "link5": (150, 0, 150),
        "gripper_link": (0, 160, 160), "camera_link0": (90, 90, 90), "camera_link": (40, 40, 40), "lidar_frame": (255, 150, 150)}
def fill_hull(pts, col):
    pts = sorted(set(pts))
    if len(pts) < 3: return
    def cross(o, a, b): return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0: lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0: up.pop()
        up.append(p)
    hull = lo[:-1] + up[:-1]
    ys = [p[1] for p in hull]
    for yy in range(max(min(ys), 0), min(max(ys), H - 1) + 1):
        xs = []
        for (x1, y1), (x2, y2) in zip(hull, hull[1:] + hull[:1]):
            if (y1 <= yy < y2) or (y2 <= yy < y1):
                xs.append(x1 + (yy - y1) * (x2 - x1) / (y2 - y1))
        if len(xs) >= 2:
            img[yy, max(int(min(xs)), 0):min(int(max(xs)), W - 1)] = col
for link, c in C.items():
    fill_hull([px(p[0], p[2]) for p in c], cols.get(link, (0, 0, 0)))
for zz in range(0, H, 1): img[zz, px(0, 0)[0]] = (255, 0, 255)    # x=0 marker (base_footprint)
open("model.ppm", "wb").write(b"P6 %d %d 255\n" % (W, H) + img.tobytes())
subprocess.run(["sips", "-s", "format", "png", "model.ppm", "--out", "model.png"], capture_output=True)
for n in ("joint1", "joint2", "joint3", "joint4"): print(n, "q=%.1f deg" % np.degrees(q[n]))
tip = F["end_effector_link"][:3, 3]; print("end effector (x,y,z) m:", tip.round(3))
