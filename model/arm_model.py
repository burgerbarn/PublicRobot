"""Arm forward kinematics + collision geometry from robot_model.json (numbers extracted from the
factory URDF/meshes). Pure numpy."""
import json
import numpy as np

def rot_rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = np.cos(r), np.sin(r), np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])

def rot_axis(a, q):
    a = np.asarray(a, float); a = a / np.linalg.norm(a)
    K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + np.sin(q) * K + (1 - np.cos(q)) * K @ K

def T(R, t):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = t; return M

class ArmModel:
    def __init__(self, path):
        self.m = json.load(open(path))
        self.k = self.m["pulse_to_rad"]["rad_per_pulse"]

    def q_from_pulses(self, pulses):
        z = self.m["pulse_to_rad"]["zero_pulse"]
        return {f"joint{s}": (z[str(s)] - pulses[s]) * self.k for s in (1, 2, 3, 4, 5)}

    def link_frames(self, q):
        """Frames of every link in base_footprint."""
        J = self.m["joints"]
        F = {"base_footprint": np.eye(4)}
        order = ["base_joint", "lidar_joint", "joint1", "joint2", "joint3", "joint4", "joint5", "gripper_joint",
                 "end_effector_joint", "camera_connect_joint", "camera_joint"]
        for name in order:
            j = J[name]
            M = F[j["parent"]] @ T(rot_rpy(*(j["rpy"] or [0, 0, 0])), j["xyz"] or [0, 0, 0])
            if j["type"] == "revolute":
                M = M @ T(rot_axis(j["axis"], q.get(name, 0.0)), [0, 0, 0])
            F[j["child"]] = M
        return F

    def box_corners(self, F):
        out = {}
        for link, b in self.m["boxes"].items():
            lo, hi = np.array(b["min"]), np.array(b["max"])
            c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            out[link] = (F[link][:3, :3] @ c.T).T + F[link][:3, 3]
        return out

    def chassis_heights(self, x, y):
        """Vectorised chassis_height (identical indexing: int() truncation, -1 outside the map)."""
        if not hasattr(self, '_Z'):
            h = self.m["chassis_heightmap"]; self._Z = np.array(h["z"], float); self._hm = (h["origin_xy"], h["cell"])
        (ox, oy), c = self._hm
        i = np.trunc((x - ox) / c).astype(int); j = np.trunc((y - oy) / c).astype(int)
        ok = (i >= 0) & (i < self._Z.shape[0]) & (j >= 0) & (j < self._Z.shape[1])
        out = np.full(len(x), -1.0)
        out[ok] = self._Z[i[ok], j[ok]]
        return out

    def chassis_height(self, x, y):
        h = self.m["chassis_heightmap"]; ox, oy = h["origin_xy"]; c = h["cell"]; Z = h["z"]
        i, j = int((x - ox) / c), int((y - oy) / c)
        if 0 <= i < len(Z) and 0 <= j < len(Z[0]):
            return Z[i][j]
        return -1.0


def obb_overlap(A, B, margin=0.0):
    """Separating-axis test between two boxes given by their 8 corners (N x 3). Batched (2026-09-29): all 15 axes
    in one array - same decisions as the per-axis loop, ~10x faster (the driver runs it at 25 Hz)."""
    def axes(P):
        e = np.stack([P[4] - P[0], P[2] - P[0], P[1] - P[0]])
        n = np.linalg.norm(e, axis=1)
        return e[n > 1e-9] / n[n > 1e-9, None]
    a, b = axes(A), axes(B)
    cr = np.cross(a[:, None, :], b[None, :, :]).reshape(-1, 3)
    cn = np.linalg.norm(cr, axis=1)
    ax = np.concatenate([a, b, cr[cn > 1e-9] / cn[cn > 1e-9, None]])
    pa, pb = A @ ax.T, B @ ax.T                                # (8, n_axes)
    sep = (pa.max(0) + margin < pb.min(0)) | (pb.max(0) + margin < pa.min(0))
    return not sep.any()


ARM_PARTS = ["link2", "link3", "link4", "link5", "gripper_link", "camera_link0", "camera_link"]
# pairs that are physically attached/adjacent (always in contact by construction) - not checked
ADJ = {frozenset(p) for p in [("link1", "link2"), ("link2", "link3"), ("link3", "link4"), ("link4", "link5"),
                                ("link5", "gripper_link"), ("link4", "gripper_link"), ("link4", "camera_link0"),
                                ("camera_link0", "camera_link"), ("link4", "camera_link")]}

def collisions(model, pulses, margin=0.005, samples=6):
    q = model.q_from_pulses(pulses); F = model.link_frames(q); C = model.box_corners(F)
    hits = []
    parts = ["link1"] + ARM_PARTS
    for i, a in enumerate(parts):
        for b in parts[i + 1:] + ["lidar_frame"]:
            if frozenset((a, b)) in ADJ or a == b:
                continue
            if a == "link1" and b == "lidar_frame":
                continue                                        # arm base sits on the lidar tower
            if obb_overlap(C[a], C[b], margin):
                hits.append((a, b))
    g = np.linspace(0, 1, samples)
    for a in ARM_PARTS:                                        # arm vs chassis height map (vectorised 2026-09-29:
        P = C[a]; o = P[0]; e1, e2, e3 = P[4] - P[0], P[2] - P[0], P[1] - P[0]   # same result, ~10x faster)
        pts = (o + g[:, None, None, None] * e1 + g[None, :, None, None] * e2 + g[None, None, :, None] * e3).reshape(-1, 3)
        if (pts[:, 2] <= model.chassis_heights(pts[:, 0], pts[:, 1]) + margin).any():
            hits.append((a, "chassis"))
    return hits
