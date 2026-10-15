"""Drop-in replacement for piper-sample-generator's generate_samples(), used by openwakeword/train.py
(config piper_sample_generator_path -> this directory). The training venv (Py3.11, numpy<2) cannot run the
current kokoro-onnx (numpy>=2), so synthesis runs in the TTS venv (~/voice/tts-venv) via kokoro_worker.py.
Writes 16 kHz mono int16 wavs (what openWakeWord expects)."""
import json, os, random, subprocess, tempfile, uuid

TTS_PY = os.path.expanduser('~/voice/tts-venv/bin/python')
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'kokoro_worker.py')


def generate_samples(text, max_samples, output_dir, length_scales=(1.0,), file_names=None,
                     batch_size=None, noise_scales=None, noise_scale_ws=None, auto_reduce_batch_size=None, **kw):
    texts = [text] if isinstance(text, str) else list(text)
    os.makedirs(output_dir, exist_ok=True)
    names = file_names or [uuid.uuid4().hex + '.wav' for _ in range(max_samples)]
    jobs = [[texts[i % len(texts)], length_scales[i % len(length_scales)], os.path.join(output_dir, names[i]),
             random.getrandbits(32)] for i in range(max_samples)]
    random.shuffle(jobs)
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(jobs, f)
    try:
        subprocess.run([TTS_PY, WORKER, f.name], check=True)
    finally:
        os.unlink(f.name)
