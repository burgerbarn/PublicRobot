"""Exhaustive map-to-map match: occupied cells of a local map vs likelihood field of a reference map,
all rotations (1 deg) x all translations (FFT correlation), reference as-is and mirrored (y flipped).
Usage: match_maps.py local.pgm ref.pgm out_prefix   (both 0.05 m/px, PGM P5)"""
import math, sys
import numpy as np
from scipy import ndimage, signal

def read_pgm(p):
    b = open(p, 'rb').read(); parts = b.split(maxsplit=4)
    w, h = int(parts[1]), int(parts[2]); return np.frombuffer(parts[4][:w * h], np.uint8).reshape(h, w)

loc, ref = read_pgm(sys.argv[1]), read_pgm(sys.argv[2]); out = sys.argv[3]
occ_l = np.argwhere(loc < 50).astype(float)             # (row, col) of local obstacles
occ_l -= occ_l.mean(0)
print('local occupied', len(occ_l), 'ref size', ref.shape)
results = {}
for tag, R in (('as-is', ref), ('mirrored', ref[::-1])):
    like = np.exp(-(ndimage.distance_transform_edt(R >= 50) * 0.05 / 0.1) ** 2)
    best = (-1, None)
    for deg in range(360):
        th = math.radians(deg); c, s = math.cos(th), math.sin(th)
        pts = occ_l @ np.array([[c, s], [-s, c]])
        pts = np.round(pts - pts.min(0)).astype(int)
        k = np.zeros(pts.max(0) + 1); np.add.at(k, (pts[:, 0], pts[:, 1]), 1.0)
        corr = signal.fftconvolve(like, k[::-1, ::-1], mode='valid') / len(occ_l)
        i = np.unravel_index(corr.argmax(), corr.shape)
        if corr[i] > best[0]: best = (float(corr[i]), (deg, i, pts))
    results[tag] = best
    print(f'{tag:8s} best score {best[0]:.3f} at rotation {best[1][0]} deg, offset {best[1][1]}')
    # overlay image: ref grey/white/black, local obstacles in red
    deg, (r0, c0), pts = best[1]
    img = np.stack([R, R, R], -1).copy()
    rr, cc = pts[:, 0] + r0, pts[:, 1] + c0
    ok = (rr >= 0) & (rr < R.shape[0]) & (cc >= 0) & (cc < R.shape[1])
    img[rr[ok], cc[ok]] = (230, 0, 0)
    h, w = img.shape[:2]
    open(f'{out}_{tag}.ppm', 'wb').write(b'P6\n%d %d\n255\n' % (w, h) + img.astype(np.uint8).tobytes())
