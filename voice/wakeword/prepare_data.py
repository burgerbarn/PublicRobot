"""Download/convert training data for openWakeWord (runs on bigbuddy, venv ~/voice/oww-train).
Sources (openWakeWord author's published data + AudioSet): all go to ~/voice/wakeword/data (delete after training).
  - davidscripka/openwakeword_features: ACAV100M negative features (17.3 GB) + validation_set_features.npy (0.18 GB)
  - davidscripka/MIT_environmental_impulse_responses: room impulse responses (272 wavs)
  - agkphysics/AudioSet bal_train shards 00-01 (~1.4 GB): background audio for augmentation
  - openwakeword base models (melspectrogram / embedding onnx)"""
import io, os, sys
import numpy as np, soundfile as sf
from scipy.signal import resample_poly
from huggingface_hub import hf_hub_download

D = os.path.expanduser('~/voice/wakeword/data')
os.makedirs(D, exist_ok=True)

def to16k(a, sr):
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != 16000:
        g = np.gcd(int(sr), 16000)
        a = resample_poly(a, 16000 // g, int(sr) // g)
    return (np.clip(a, -1, 1) * 32767).astype(np.int16)

step = sys.argv[1] if len(sys.argv) > 1 else 'all'

if step in ('all', 'models'):
    import openwakeword.utils as u
    u.download_models(model_names=['__none__'])     # base feature models only
    print('base models ok', flush=True)

if step in ('all', 'rir'):
    out = os.path.join(D, 'rir'); os.makedirs(out, exist_ok=True)
    from huggingface_hub import snapshot_download
    p = snapshot_download('davidscripka/MIT_environmental_impulse_responses', repo_type='dataset',
                          local_dir=os.path.join(D, 'rir_src'))
    n = 0
    for root, _, files in os.walk(p):
        for f in files:
            if f.lower().endswith('.wav'):
                a, sr = sf.read(os.path.join(root, f), dtype='float32')
                sf.write(os.path.join(out, f), to16k(a, sr), 16000, subtype='PCM_16'); n += 1
    print('rir wavs', n, flush=True)

if step in ('all', 'background'):
    import pyarrow.parquet as pq
    out = os.path.join(D, 'background'); os.makedirs(out, exist_ok=True)
    n = 0
    for shard in ('00', '01'):
        p = hf_hub_download('agkphysics/AudioSet', f'data/bal_train/{shard}.parquet', repo_type='dataset',
                            local_dir=os.path.join(D, 'audioset_src'))
        t = pq.read_table(p, columns=['audio'])
        for row in t.column('audio').to_pylist():
            try:
                a, sr = sf.read(io.BytesIO(row['bytes']), dtype='float32')
            except Exception:
                continue
            sf.write(os.path.join(out, f'as_{shard}_{n:05d}.wav'), to16k(a, sr), 16000, subtype='PCM_16'); n += 1
        os.remove(p)                                 # keep only the converted wavs
    print('background wavs', n, flush=True)

if step in ('all', 'features'):
    for f in ('validation_set_features.npy', 'openwakeword_features_ACAV100M_2000_hrs_16bit.npy'):
        p = hf_hub_download('davidscripka/openwakeword_features', f, repo_type='dataset', local_dir=D)
        print('feature file', p, os.path.getsize(p) / 1e9, 'GB', flush=True)
print('DONE', step)
