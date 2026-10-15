"""Self-training: learn the owner's face from what the robot mind has seen (no labelling needed up front).
Frames: ~/vision/mind/*/*.jpg (acquisitions) + ~/vision/attention/*/*.jpg (test runs).
facenet-pytorch (MIT; pip --no-deps to keep the Jetson torch): MTCNN face detection -> InceptionResnetV1 (VGGFace2)
512-d embeddings, L2-normalised. The owner = the largest CHAIN of similar faces (cosine >= SIM, single linkage): he is
the person the robot sees most. Output ~/vision/owner/: owner.npz (embeddings + mean), sheet.jpg (every face, green =
owner group), stats. The owner confirms the sheet once; after that recognition is automatic.
Usage (robot, ~/vision/venv): python owner_faces.py [--sim 0.6]"""
import argparse, glob, json, os

import cv2
import numpy as np
import torch
from facenet_pytorch import MTCNN, InceptionResnetV1
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'owner')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim', type=float, default=0.5)
    ap.add_argument('--min-prob', type=float, default=0.9)
    ap.add_argument('--min-px', type=int, default=24)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    mtcnn = MTCNN(image_size=160, margin=14, keep_all=True, device=dev)
    net = InceptionResnetV1(pretrained='vggface2').eval().to(dev)
    files = sorted(glob.glob(os.path.join(HERE, 'mind', '*', '*.jpg')) + glob.glob(os.path.join(HERE, 'attention', '*', '*.jpg')))
    embs, crops, srcs = [], [], []
    for f in files:
        img = Image.open(f).convert('RGB')
        boxes, probs = mtcnn.detect(img)
        if boxes is None:
            continue
        keep = [i for i, (b, p) in enumerate(zip(boxes, probs)) if p >= a.min_prob and min(b[2] - b[0], b[3] - b[1]) >= a.min_px]
        if not keep:
            continue
        faces = mtcnn.extract(img, boxes[keep], None)                 # (k, 3, 160, 160), normalised
        with torch.no_grad():
            e = torch.nn.functional.normalize(net(faces.to(dev)), dim=1).cpu().numpy()
        for i, k in enumerate(keep):
            x0, y0, x1, y1 = [int(v) for v in boxes[k]]
            crop = cv2.cvtColor(np.array(img)[max(0, y0):y1, max(0, x0):x1], cv2.COLOR_RGB2BGR)
            embs.append(e[i]); crops.append(crop); srcs.append((os.path.relpath(f, HERE), [x0, y0, x1, y1], float(probs[k])))
    print(f'{len(files)} frames -> {len(embs)} faces', flush=True)
    if not embs:
        raise SystemExit('no faces found')
    E = np.stack(embs); S = E @ E.T
    # connected components at SIM (single linkage): front / profile / looking-down views of the same person join
    # through the in-between poses (a single-medoid group kept only one pose - 2026-09-30)
    lab = -np.ones(len(E), int); c = 0
    for i in range(len(E)):
        if lab[i] >= 0:
            continue
        stack = [i]; lab[i] = c
        while stack:
            j = stack.pop()
            for k in np.flatnonzero((S[j] >= a.sim) & (lab < 0)):
                lab[k] = c; stack.append(k)
        c += 1
    group = np.flatnonzero(lab == np.bincount(lab).argmax())
    mean = E[group].mean(0); mean /= np.linalg.norm(mean)
    others = np.setdiff1d(np.arange(len(E)), group)
    stats = {'frames': len(files), 'faces': len(E), 'owner_group': int(len(group)), 'sim': a.sim,
             'within_group_sim_min': float(S[np.ix_(group, group)].min()),
             'to_mean_min': float((E[group] @ mean).min()),
             'others_to_mean_max': float((E[others] @ mean).max()) if len(others) else None}
    np.savez(os.path.join(OUT, 'owner.npz'), emb=E[group], mean=mean)
    json.dump({'stats': stats, 'group': [srcs[i] for i in group], 'others': [srcs[i] for i in others]},
              open(os.path.join(OUT, 'owner.json'), 'w'), indent=1)
    tiles = []
    for i, c in enumerate(crops):                                     # contact sheet: green = owner group
        t = cv2.resize(c, (96, 96)) if c.size else np.zeros((96, 96, 3), np.uint8)
        col = (0, 200, 0) if i in set(group) else (0, 0, 220)
        t = cv2.copyMakeBorder(t, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=col)
        tiles.append(t)
    per = 12
    rows = [np.hstack(tiles[i:i + per] + [np.zeros_like(tiles[0])] * (per - len(tiles[i:i + per]))) for i in range(0, len(tiles), per)]
    cv2.imwrite(os.path.join(OUT, 'sheet.jpg'), np.vstack(rows))
    print(json.dumps(stats), flush=True)


if __name__ == '__main__':
    main()
