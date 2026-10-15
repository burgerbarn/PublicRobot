#!/bin/bash
# Free the GPU for training: stop Buddy's voice loop (Whisper), unload LM Studio models, stop its API server.
systemctl --user stop buddy-voice
~/.lmstudio/bin/lms unload --all
~/.lmstudio/bin/lms server stop
sleep 3
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
