"""Turn the owner's report reviews into stored examples ("taught fingerprints") the robot uses on every scan.
Input: an explore dataset folder (raw frames + labels.json) and the review documents from the Den Look-Around page.
For each reviewed card: re-detect the raw frame (same prompts/threshold as explore -> the same boxes), pick the box(es)
the review talks about, and store (label, OWLv2 class embedding, crop). Output ~/vision/examples/: examples.npz
(embeddings + labels), examples.json (sources) and check_sheet.png (every crop with its taught label).
Usage (robot, ~/vision/venv): python teach.py dataset/<UTC> reviews_dir/"""
import argparse, glob, json, os

import cv2
import numpy as np
import PIL.Image

from explore import DECOYS, TARGETS
from scan import make_detector

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'examples')
# The owner's notes that name boxes by position (2026-09-29 review), as selectors on the card's own label.
# center = the card's box nearest the image centre; others pick among that label's boxes by position.
RULES = {
    'c0_01_speaker_confirmed': [('bottom2', 'speaker'), ('top', 'desk lamp')],
    'c0_02_speaker_confirmed': [('bottomright', 'case')],
    'c0_05_microphone_confirmed': [('top', 'guitar')],
    'c0_18_speaker_confirmed': [('xsort', ['arcade machine', 'computer monitor', 'speaker'])],
    # check sheet 2026-09-29: the centre box on cards 07 ('stand') and 11 ('right', speaker) is the owner's black
    # baseball cap - the stand/speaker he meant are other boxes, not recoverable, so these teach the cap only
    'c0_07_speaker_confirmed': [('center', 'baseball cap')],
    'c0_11_speaker_confirmed': [('center', 'baseball cap')],
}


def canon(s):
    s = s.strip().lower()
    return {'arcade machine': 'arcade machine', 'cabinet': 'cabinet'}.get(s, s)


def pick(sel, boxes, w, h):
    """boxes: [(label, score, box, emb)] of the card's label -> list of (box tuple, label)."""
    kind, lab = sel
    cy = lambda d: (d[2][1] + d[2][3]) / 2
    cx = lambda d: (d[2][0] + d[2][2]) / 2
    if kind == 'center':
        return [(min(boxes, key=lambda d: (cx(d) - w / 2) ** 2 + (cy(d) - h / 2) ** 2), lab)]
    if kind == 'bottom2':
        return [(d, lab) for d in sorted(boxes, key=cy)[-2:]]
    if kind == 'top':
        return [(min(boxes, key=cy), lab)]
    if kind == 'bottomright':
        return [(max(boxes, key=lambda d: cx(d) + cy(d)), lab)]
    if kind == 'xsort':
        return list(zip(sorted(boxes, key=cx), lab))
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dataset')
    ap.add_argument('reviews')
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    labels = {e['frame'][:-4]: e for e in json.load(open(os.path.join(a.dataset, 'labels.json'))) if 'frame' in e}
    text = TARGETS + DECOYS
    detect = make_detector('owlv2', '16', text, decoys=DECOYS)

    rev_files = sorted(glob.glob(os.path.join(a.reviews, '*.json')))
    ex_emb, ex_lab, ex_meta, crops = [], [], [], []
    for f in rev_files:
        rid = os.path.basename(f)[:-5]
        r = json.load(open(f)); r = r.get('data', r)
        if rid not in labels or not r.get('verdict'):
            continue
        e = labels[rid]
        img = PIL.Image.open(os.path.join(a.dataset, e['frame'].replace('.png', '_raw.png'))).convert('RGB')
        w, h = img.size
        dets = detect(img, 0.15, with_embeds=True)
        card = e['label']
        mine = [d for d in dets if d[0] == card] or [max((d for d in dets if d[0] in TARGETS), key=lambda d: d[1], default=None)]
        mine = [d for d in mine if d is not None]
        if not mine:
            print(f'{rid}: no box to learn from, skipped'); continue
        noun = card.replace('a ', '')
        if rid in RULES:
            sels = RULES[rid]
        elif r['verdict'] == 'right':
            sels = [('center', noun)]
        elif r.get('actual'):
            sels = [('center', canon(r['actual']))]
        else:
            sels = [('center', f'not a {noun}')]
        for sel in sels:
            for d, lab in pick(sel, mine, w, h):
                x0, y0, x1, y1 = [int(max(0, v)) for v in d[2]]
                crop = cv2.cvtColor(np.array(img)[y0:y1, x0:x1], cv2.COLOR_RGB2BGR)
                n = len(ex_lab)
                cv2.imwrite(os.path.join(OUT, f'ex{n:02d}_{lab.replace(" ", "_")}.png'), crop)
                ex_emb.append(d[3].numpy()); ex_lab.append(lab)
                ex_meta.append({'n': n, 'label': lab, 'from': rid, 'box': [x0, y0, x1, y1], 'det': d[0], 'score': round(d[1], 3),
                                'review': r})
                crops.append((crop, lab, rid))
                print(f'{rid}: box ({x0},{y0})-({x1},{y1}) {d[0]} {d[1]:.2f} -> "{lab}"')
    np.savez(os.path.join(OUT, 'examples.npz'), emb=np.stack(ex_emb), labels=np.array(ex_lab))
    json.dump(ex_meta, open(os.path.join(OUT, 'examples.json'), 'w'), indent=1)
    # check sheet: every crop with its taught label
    tiles = []
    for crop, lab, rid in crops:
        t = np.full((150, 180, 3), 255, np.uint8)
        c = crop if crop.size else np.zeros((10, 10, 3), np.uint8)
        k = min(170 / c.shape[1], 110 / c.shape[0])
        c = cv2.resize(c, (max(1, int(c.shape[1] * k)), max(1, int(c.shape[0] * k))))
        t[5:5 + c.shape[0], 5:5 + c.shape[1]] = c
        cv2.putText(t, lab[:22], (5, 132), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
        cv2.putText(t, rid.split('_')[1], (5, 146), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (90, 90, 90), 1)
        tiles.append(t)
    rows = [np.hstack(tiles[i:i + 6] + [np.full((150, 180, 3), 255, np.uint8)] * (6 - len(tiles[i:i + 6]))) for i in range(0, len(tiles), 6)]
    cv2.imwrite(os.path.join(OUT, 'check_sheet.png'), np.vstack(rows))
    # how separable are the taught labels? leave-one-out nearest neighbour on the stored fingerprints
    E = np.stack(ex_emb); L = np.array(ex_lab); S = E @ E.T; np.fill_diagonal(S, -1)
    same = [S[i, j] for i in range(len(L)) for j in range(len(L)) if i < j and L[i] == L[j]]
    diff = [S[i, j] for i in range(len(L)) for j in range(len(L)) if i < j and L[i] != L[j]]
    print(f'{len(L)} examples, {len(set(L))} labels. cosine same-label: min {min(same) if same else float("nan"):.2f} '
          f'mean {np.mean(same) if same else float("nan"):.2f} | different-label: max {max(diff):.2f} mean {np.mean(diff):.2f}')


if __name__ == '__main__':
    main()
