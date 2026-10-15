"""Score a wake-word model on real recordings of individual utterances (one wav per utterance, 16 kHz).
Each clip is padded with 1 s silence before/after and streamed through openWakeWord in 80 ms frames;
the clip's score = max model output. Usage: eval_real.py model.onnx clip_dir [clip_dir...]"""
import glob, sys
import numpy as np, soundfile as sf
from openwakeword.model import Model

m = Model(wakeword_models=[sys.argv[1]], inference_framework='onnx')
key = list(m.models.keys())[0]
for d in sys.argv[2:]:
    scores = []
    for f in sorted(glob.glob(d + '/*.wav')):
        a, sr = sf.read(f, dtype='int16')
        a = np.concatenate([np.zeros(16000, np.int16), a, np.zeros(16000, np.int16)])
        m.reset()
        scores.append(max(m.predict(a[i:i + 1280])[key] for i in range(0, len(a) - 1280, 1280)))
    s = np.array(scores)
    print(f'{d}: n={len(s)} detected@0.5 {int((s >= 0.5).sum())}/{len(s)} @0.3 {int((s >= 0.3).sum())}/{len(s)}  '
          f'scores {np.round(np.sort(s), 2).tolist()}')
