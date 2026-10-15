"""Open-vocabulary detection on the robot (NanoOWL = OWL-ViT with a TensorRT image encoder, NVIDIA-AI-IOT, Apache-2.0).
Run in ~/vision/venv. Usage:
  python detect.py frame_rgb.png [--depth frame_depth.npy] [--prompts "a microphone,a television,a speaker"] [--th 0.1]
Writes <image>_det.png (boxes) and prints label, score, box and median depth inside the box (metres; depth is
the Aurora depth image at the same 640x400 size, assumed roughly aligned with RGB - first cut)."""
import argparse, os, time

import cv2
import numpy as np
import PIL.Image
from nanoowl.owl_predictor import OwlPredictor

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = 'a microphone,a television,a speaker'

ap = argparse.ArgumentParser()
ap.add_argument('image')
ap.add_argument('--depth')
ap.add_argument('--prompts', default=PROMPTS)
ap.add_argument('--th', type=float, default=0.1)
ap.add_argument('--model', default='16', choices=['16', '32'], help='OWL-ViT base patch size (16 = finer, slower)')
a = ap.parse_args()

t = time.time()
pred = OwlPredictor(f'google/owlvit-base-patch{a.model}',
                    image_encoder_engine=os.path.join(HERE, 'engines', f'owl_image_encoder_patch{a.model}.engine'))
text = [p.strip() for p in a.prompts.split(',') if p.strip()]
enc = pred.encode_text(text)
print(f'load {time.time() - t:.1f}s')

img = PIL.Image.open(a.image).convert('RGB')
for i in range(3):                                   # first call warms up
    t = time.time()
    out = pred.predict(image=img, text=text, text_encodings=enc, threshold=a.th, pad_square=False)
    dt = time.time() - t
print(f'predict {dt * 1000:.0f} ms')

depth = np.load(a.depth) if a.depth else None
vis = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
for lab, sc, box in zip(out.labels.tolist(), out.scores.tolist(), out.boxes.tolist()):
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    d = ''
    if depth is not None:
        cx0, cx1 = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
        cy0, cy1 = y0 + (y1 - y0) // 4, y1 - (y1 - y0) // 4
        roi = depth[max(0, cy0):cy1, max(0, cx0):cx1]
        v = roi[roi > 0]
        d = f' depth {np.median(v) / 1000:.2f} m ({v.size / max(1, roi.size):.0%} valid)' if v.size else ' depth n/a'
    print(f'{text[lab]:14} {sc:.2f} box ({x0},{y0})-({x1},{y1}){d}')
    cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 200, 255), 2)
    cv2.putText(vis, f'{text[lab]} {sc:.2f}', (x0, max(12, y0 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1)
cv2.imwrite(os.path.splitext(a.image)[0] + '_det.png', vis)
