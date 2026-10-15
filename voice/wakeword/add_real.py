"""Add the owner's real recordings to the openWakeWord training set (run on bigbuddy, oww-train venv).
Positives: every real/owner_pos_*.wav (one "yo buddy" per utterance, cut by energy) -> clips in real/pos_clips;
  every 4th clip held out -> real/pos_test; the rest copied POS_REPS times into work/yo_buddy/positive_train.
Negatives: real/owner_neg_1_16k.wav (talking) and real/neg_media_*.wav (echo-cancelled music/movie, 2026-09-28):
  first 75 % of each cut into 2 s chunks (1 s hop) copied NEG_REPS times into negative_train; last 25 % of each
  -> real/neg_test_<name>.wav. Re-running replaces earlier owner_* / real_* files."""
import glob, os, shutil
import numpy as np, soundfile as sf
import scipy.signal as ss

W = os.path.expanduser('~/voice/wakeword')
POS_REPS, NEG_REPS = 120, 15
pt, nt = f'{W}/work/yo_buddy/positive_train', f'{W}/work/yo_buddy/negative_train'
for d in (pt, nt):
    for f in glob.glob(f'{d}/owner_*.wav') + glob.glob(f'{d}/real_*.wav'):
        os.remove(f)
for d in ('pos_clips', 'pos_test'):
    shutil.rmtree(f'{W}/real/{d}', ignore_errors=True); os.makedirs(f'{W}/real/{d}')


def to16k(path):
    a, sr = sf.read(path, dtype='float32')
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != 16000:
        a = ss.resample_poly(ss.sosfilt(ss.butter(4, 100, btype='high', fs=sr, output='sos'), a), 16000, sr)
    return a.astype(np.float32)


def utterances(y, sr=16000):
    f = int(0.02 * sr)
    db = 20 * np.log10(np.array([np.sqrt(np.mean(y[i:i + f] ** 2)) for i in range(0, len(y) - f, f)]) + 1e-9)
    on = db > np.percentile(db, 20) + 12
    segs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and (on[j] or (j + 15 < len(on) and on[j:j + 15].any())):
                j += 1
            segs.append((i, j)); i = j
        else:
            i += 1
    return [(s * f, e * f) for s, e in segs if 0.3 <= (e - s) * 0.02 <= 2.2]


k = 0
for rec in sorted(glob.glob(f'{W}/real/owner_pos_*.wav')):
    if rec.endswith('_16k.wav'):
        continue
    y = to16k(rec)
    for s, e in utterances(y):
        c = y[max(0, s - 2400):min(len(y), e + 2400)]
        c = c / max(1e-6, np.abs(c).max()) * 0.7
        sf.write(f'{W}/real/pos_clips/owner_{k:03d}.wav', (c * 32767).astype(np.int16), 16000, subtype='PCM_16')
        k += 1
clips = sorted(glob.glob(f'{W}/real/pos_clips/owner_*.wav'))
n_tr = 0
for i, f in enumerate(clips):
    if i % 4 == 3:
        shutil.copy(f, f'{W}/real/pos_test/'); continue
    for r in range(POS_REPS):
        shutil.copy(f, f'{pt}/owner_{i:03d}_{r:03d}.wav')
    n_tr += 1

neg_sources = [f'{W}/real/owner_neg_1_16k.wav'] + sorted(glob.glob(f'{W}/real/neg_media_*.wav'))
n_neg = 0
for src in neg_sources:
    if not os.path.exists(src):
        continue
    name = os.path.basename(src).replace('.wav', '')
    a, sr = sf.read(src, dtype='int16')
    cut = int(len(a) * 0.75)
    sf.write(f'{W}/real/neg_test_{name}.wav', a[cut:], sr, subtype='PCM_16')
    for s in range(0, cut - 2 * sr, sr):
        for r in range(NEG_REPS):
            sf.write(f'{nt}/real_{name}_{s // sr:04d}_{r:02d}.wav', a[s:s + 2 * sr], sr, subtype='PCM_16')
        n_neg += 1
print(f'positives: {len(clips)} clips from {len(glob.glob(f"{W}/real/owner_pos_*.wav"))} recordings, '
      f'{n_tr} train x{POS_REPS}, {len(clips) - n_tr} held out; negatives: {n_neg} chunks x{NEG_REPS} from '
      f'{len(neg_sources)} sources')
