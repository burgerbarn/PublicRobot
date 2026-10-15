#!/bin/bash
# Launcher for buddy_voice.py (systemd --user buddy-voice.service). CUDA libs for faster-whisper come from
# the venv's nvidia-cublas/cudnn wheels.
V=/home/burgerbarn/voice/venv
SP=$($V/bin/python -c "import site; print(site.getsitepackages()[0])")
export LD_LIBRARY_PATH=$SP/nvidia/cublas/lib:$SP/nvidia/cudnn/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
# mic gain AFTER the echo canceller (owner 2026-10-05, XVF3800 speech beam has no AGC: speech peaked at -22 dBFS;
# with +12 dB about -8 dBFS, no clipping). PipeWire does not keep this across restarts, so it is set at every start.
# 2026-10-06: 0 dB - robot_mic already carries +12 dB; +12 here too clipped the mic 9% of the time (owner: "very unstable")
for i in 1 2 3 4 5 6 7 8 9 10; do pactl set-source-volume buddy_aec_source 12dB 2>/dev/null && break; sleep 1; done
exec $V/bin/python /home/burgerbarn/voice/buddy_voice.py "$@"
