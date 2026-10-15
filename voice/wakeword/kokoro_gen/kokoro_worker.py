"""Kokoro synthesis worker (runs in ~/voice/tts-venv). Input: JSON list of [text, length_scale, path, seed].
English voices + random 2-voice blends, speed from length_scale with jitter, punctuation variants, 16 kHz int16."""
import json, os, random, shutil, sys, tempfile
from multiprocessing import Pool
import numpy as np, soundfile as sf
from scipy.signal import resample_poly

MODEL = os.path.expanduser('~/voice/models/kokoro/kokoro-v1.0.onnx')
VOICES = os.path.expanduser('~/voice/models/kokoro/voices-v1.0.bin')
_k = None
_names = None
_tmp = None


def _init():
    # Each worker holds its own model: keep threads/memory small (14 default workers OOM-killed bigbuddy's
    # desktop, 2026-09-28). KOKORO_PROCS workers x KOKORO_THREADS onnxruntime threads.
    global _k, _names, _tmp
    # phonemizer-fork copies libespeak-ng.so (648 KB) into a NEW tempdir on every phonemize call and never
    # removes it: 10k clips = 6.3 GB in RAM-backed /tmp (2026-09-28). Private on-disk TMPDIR, emptied per clip.
    _tmp = tempfile.mkdtemp(prefix=f'kokoro-{os.getpid()}-', dir=os.path.expanduser('~/voice/wakeword/tmp'))
    os.environ['TMPDIR'] = _tmp
    tempfile.tempdir = _tmp
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(os.environ.get('KOKORO_THREADS', '2'))
    so.inter_op_num_threads = 1
    so.enable_cpu_mem_arena = False
    so.enable_mem_pattern = False                  # per-shape buffers grew RSS to OOM (16G scope) over ~2k clips
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import kokoro_patch
    kokoro_patch.apply()                               # reuse one espeak backend (per-call leak fix)
    _k = Kokoro.from_session(ort.InferenceSession(MODEL, so, providers=['CPUExecutionProvider']), VOICES)
    _names = [v for v in _k.get_voices() if v[:2] in ('af', 'am', 'bf', 'bm')]


def _one(job):
    text, length_scale, path, seed = job
    rnd = random.Random(seed)
    a_name, b_name = rnd.sample(_names, 2)
    w = rnd.random() if rnd.random() < 0.5 else 1.0          # half pure voices, half blends
    style = w * _k.get_voice_style(a_name) + (1 - w) * _k.get_voice_style(b_name)
    lang = 'en-gb' if a_name.startswith('b') else 'en-us'
    speed = float(np.clip((1.0 / length_scale) * rnd.uniform(0.9, 1.1), 0.6, 1.6))
    t = rnd.choice([text, text + '.', text + '!', text + '?', text.capitalize() + '!'])
    try:
        a, sr = _k.create(t, voice=style, speed=speed, lang=lang)
    except Exception as e:
        print('skip', repr(t), e, file=sys.stderr)
        return 0
    for d in os.listdir(_tmp):                                  # drop espeak lib copies (already dlopen'ed)
        shutil.rmtree(os.path.join(_tmp, d), ignore_errors=True)
    a = resample_poly(a, 2, 3)                                  # 24 kHz -> 16 kHz
    a = a / max(1e-6, np.abs(a).max()) * rnd.uniform(0.3, 0.9)
    sf.write(path, (a * 32767).astype(np.int16), 16000, subtype='PCM_16')
    return 1


if __name__ == '__main__':
    jobs = json.load(open(sys.argv[1]))
    os.makedirs(os.path.expanduser('~/voice/wakeword/tmp'), exist_ok=True)
    _init()                                                     # fail fast here, not in every pool worker
    n = 0
    with Pool(int(os.environ.get('KOKORO_PROCS', '4')), initializer=_init,
              maxtasksperchild=int(os.environ.get('KOKORO_RECYCLE', '200'))) as p:   # bound any growth
        for i, r in enumerate(p.imap_unordered(_one, jobs, chunksize=8)):
            n += r
            if i % 1000 == 0:
                print(f'kokoro: {i}/{len(jobs)}', flush=True)
    print(f'kokoro: wrote {n}/{len(jobs)}', flush=True)
    if n < 0.9 * len(jobs):
        sys.exit(f'kokoro: only {n}/{len(jobs)} clips written')
