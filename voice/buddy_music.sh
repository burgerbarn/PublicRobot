#!/bin/bash
# Music straight to the Evo 150 over USB (owner 2026-10-01: direct, clean - no echo-cancel, no Chromium).
# Shuffled tracks from the jukebox server -> mpv -> PipeWire sink of the Evo USB device. Runs as user unit buddy-music.
# Usage: buddy_music.sh [limit]   (stop: systemctl --user stop buddy-music)
EVO=alsa_output.usb-Cambridge_Audio_Evo_150_00403a6e-00.analog-stereo
curl -s -m 10 "http://127.0.0.1:8281/api/browse?limit=${1:-500}" | python3 -c "
import sys,json,random; t=json.load(sys.stdin)['tracks']; random.shuffle(t)
open('/tmp/buddy-shuffle.m3u','w').write('\n'.join('http://127.0.0.1:8281'+x['stream_url'] for x in t if x.get('stream_url')))"
systemctl --user stop buddy-music.service 2>/dev/null; systemctl --user reset-failed buddy-music.service 2>/dev/null
systemd-run --user -q --unit=buddy-music --collect mpv --no-video --really-quiet --audio-device=pipewire/$EVO \
  --playlist=/tmp/buddy-shuffle.m3u
