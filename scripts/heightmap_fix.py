"""Rebuild chassis height map by sampling each triangle's surface (not its bbox max). Run on robot."""
import json, struct, sys
import numpy as np
mesh, model_path = sys.argv[1], sys.argv[2]
b = open(mesh, "rb").read(); n = struct.unpack("<I", b[80:84])[0]
tri = np.frombuffer(b[84:84 + n * 50], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))["v"].reshape(-1, 3, 3).astype(float)
m = json.load(open(model_path)); h = m["chassis_heightmap"]; ox, oy = h["origin_xy"]; c = h["cell"]
Z = np.full(np.array(h["z"]).shape, -1.0)
for t in tri:
    e = max(np.linalg.norm(t[1] - t[0]), np.linalg.norm(t[2] - t[0]), np.linalg.norm(t[2] - t[1]))
    k = max(2, int(np.ceil(e / (c / 3))))            # sample spacing <= cell/3
    for i in range(k + 1):
        for j in range(k + 1 - i):
            p = t[0] + (t[1] - t[0]) * i / k + (t[2] - t[0]) * j / k
            a, bb = int((p[0] - ox) / c), int((p[1] - oy) / c)
            if 0 <= a < Z.shape[0] and 0 <= bb < Z.shape[1]:
                Z[a, bb] = max(Z[a, bb], p[2])
old = np.array(h["z"])
print("cells changed:", int((np.abs(old - Z) > 0.005).sum()), "of", Z.size, "| max z %.3f" % Z.max())
h["z"] = np.round(Z, 4).tolist(); h["method"] = "triangle surface sampling, spacing <= cell/3"
json.dump(m, open(model_path, "w"))
