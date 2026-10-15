"""Extract a numeric robot model (joints, link boxes, chassis height map) from the factory URDF +
meshes on the read-only mounted factory partition. Output contains measurements only (no meshes).
Usage: extract_robot_model.py <rosorin_description dir> <out.json>
"""
import glob, json, re, struct, sys
import numpy as np

D, OUT = sys.argv[1], sys.argv[2]
X = {}
for f in glob.glob(D + "/urdf/pro/*.xacro"):
    X[f.split("/")[-1]] = open(f).read()

def joints(src):
    out = {}
    for m in re.finditer(r'<joint\s+name="([^"]+)"\s+type="([^"]+)"(.*?)</joint>', src, re.S):
        name, typ, b = m.groups()
        g = lambda tag, attr: (re.search(rf'<{tag}[^>]*{attr}="([^"]*)"', b) or [None, None])[1]
        num = lambda s: [float(v) for v in s.replace("${-M_PI/2}", str(-np.pi / 2)).split()] if s else None
        lim = re.search(r'<limit[^>]*lower="([^"]+)"[^>]*upper="([^"]+)"', b)
        out[name] = {"type": typ, "parent": g("parent", "link").replace("$(arg prefix)", ""),
                     "child": g("child", "link").replace("$(arg prefix)", ""), "xyz": num(g("origin", "xyz")),
                     "rpy": num(g("origin", "rpy")), "axis": num(g("axis", "xyz")),
                     "limit": [float(lim.group(1)), float(lim.group(2))] if lim else None}
    return out

J = {}
for fn in ("arm.urdf.xacro", "gripper.urdf.xacro", "depth_camera.urdf.xacro", "lidar.urdf.xacro", "car_mecanum.urdf.xacro"):
    J.update(joints(X[fn]))
J = {k: v for k, v in J.items() if not k.startswith(("wheel", "l_", "r_", "depth_cam_joint_sim"))}

def load(p):
    b = open(p, "rb").read()
    if b[:5] == b"solid" and b"facet" in b[:400]:
        return np.array([list(map(float, l.split()[1:4])) for l in b.decode(errors="ignore").splitlines()
                         if l.strip().startswith("vertex")]).reshape(-1, 3, 3)
    n = struct.unpack("<I", b[80:84])[0]
    a = np.frombuffer(b[84:84 + n * 50], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    return a["v"].reshape(-1, 3, 3).astype(float)

M = D + "/meshes/pro"
boxes = {}
for link, path in {"link1": "arm/link1.stl", "link2": "arm/link2.stl", "link3": "arm/link3.stl", "link4": "arm/link4.stl",
                   "link5": "arm/link5.stl", "gripper_link": "arm/gripper_link.stl", "camera_link0": "arm/camera_connect_link.stl",
                   "camera_link": "arm/camera_link.stl", "lidar_frame": "common/lidar_link.stl"}.items():
    t = load(f"{M}/{path}").reshape(-1, 3)
    boxes[link] = {"min": t.min(0).round(5).tolist(), "max": t.max(0).round(5).tolist()}

# chassis height map in base_footprint frame (mesh frame), 1 cm cells, conservative (triangle max z over its xy bbox)
tri = load(f"{M}/mecanum/base_link.stl")
cell = 0.01
lo = tri.reshape(-1, 3).min(0); hi = tri.reshape(-1, 3).max(0)
nx, ny = int(np.ceil((hi[0] - lo[0]) / cell)) + 1, int(np.ceil((hi[1] - lo[1]) / cell)) + 1
H = np.full((nx, ny), -1.0)
for t in tri:
    i0, i1 = int((t[:, 0].min() - lo[0]) / cell), int((t[:, 0].max() - lo[0]) / cell)
    j0, j1 = int((t[:, 1].min() - lo[1]) / cell), int((t[:, 1].max() - lo[1]) / cell)
    H[i0:i1 + 1, j0:j1 + 1] = np.maximum(H[i0:i1 + 1, j0:j1 + 1], t[:, 2].max())
model = {"source": "factory URDF/meshes on APP_old, measured 2026-09-27 (numbers only)",
         "pulse_to_rad": {"rad_per_pulse": 240 / 360 * 2 * np.pi / 1000, "zero_pulse": {"1": 500, "2": 500, "3": 500, "4": 500, "5": 500},
                          "flipped": True, "note": "factory: rad = (zero - pulse) * k when flipped (servo_controller min 1000 / max 0)"},
         "joints": J, "boxes": boxes,
         "chassis_heightmap": {"frame": "base_footprint", "origin_xy": lo[:2].round(5).tolist(), "cell": cell,
                               "z": np.round(H, 4).tolist()}}
json.dump(model, open(OUT, "w"))
print("joints:", list(J)); print("boxes:", list(boxes)); print("heightmap", H.shape, "z range %.3f..%.3f" % (H[H > -1].min(), H.max()))
