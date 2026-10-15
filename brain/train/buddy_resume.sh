#!/bin/bash
# Put Buddy back as it was before buddy_pause.sh: LM Studio server + Gemma 4 12B (ctx 12288, parallel 4, ttl 1h), voice loop.
~/.lmstudio/bin/lms server start --bind 0.0.0.0   # LAN: the server's Open WebUI uses 192.168.1.111:1234 (owner 2026-10-15)
~/.lmstudio/bin/lms load google/gemma-4-12b --context-length 12288 --parallel 4 --ttl 3600 -y
systemctl --user start buddy-voice
sleep 15
systemctl --user is-active buddy-voice buddy-video buddy-jukebox buddy-screen
~/.lmstudio/bin/lms ps
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
