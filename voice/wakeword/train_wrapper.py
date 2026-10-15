"""Run openwakeword/train.py with torchaudio.load/info backed by soundfile.
torchaudio >= 2.9 routes load() through torchcodec (+ system FFmpeg) and dropped info(); training only
reads local WAV files, which soundfile handles. Usage: train_wrapper.py <train.py args...>"""
import os, runpy, sys
from types import SimpleNamespace
import soundfile as sf
import torch
import torchaudio


def _load(path, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, format=None, **kw):
    stop = None if num_frames is None or num_frames < 0 else frame_offset + num_frames
    dtype = 'float32' if normalize else 'int16'
    data, sr = sf.read(path, start=frame_offset, stop=stop, dtype=dtype, always_2d=True)
    t = torch.from_numpy(data.T.copy() if channels_first else data.copy())
    return t, sr


def _info(path, format=None, **kw):
    i = sf.info(path)
    return SimpleNamespace(sample_rate=i.samplerate, num_frames=i.frames, num_channels=i.channels,
                           bits_per_sample=16 if 'PCM_16' in i.subtype else 0, encoding=i.subtype)


torchaudio.load = _load
torchaudio.info = _info
import openwakeword
train_py = os.path.join(os.path.dirname(openwakeword.__file__), 'train.py')
sys.argv = [train_py] + sys.argv[1:]
runpy.run_path(train_py, run_name='__main__')
