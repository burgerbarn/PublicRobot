#!/bin/bash
# Record what Buddy hears (echo-cancelled mic) while media plays, as wake-word NEGATIVES.
# Pauses buddy-voice during recording so it can't react. Output: ~/voice/wakeword/real/neg_media_<tag>.wav (16 kHz)
set -uo pipefail
MUSIC_S=${1:-300}; MOVIE_S=${2:-120}; D=~/voice/wakeword/real; mkdir -p $D; cd ~/voice
V=~/voice/venv/bin/python; T=$(date +%H%M)
systemctl --user stop buddy-voice.service
rec() { timeout $2 parec --device=buddy_aec_source --rate=16000 --channels=1 --format=s16le --raw > /tmp/rec_$1.raw
        $V -c "import numpy as np, soundfile as sf; a=np.fromfile('/tmp/rec_$1.raw', np.int16); sf.write('$D/neg_media_$1.wav', a, 16000, subtype='PCM_16'); print('$1', len(a)/16000, 's')"; }
$V media_screen.py music >/dev/null 2>&1; sleep 5; rec music_$T $MUSIC_S
$V media_screen.py movie >/dev/null 2>&1; sleep 10; rec movie_$T $MOVIE_S
$V media_screen.py control stop >/dev/null 2>&1
systemctl --user start buddy-voice.service
echo RECORD_DONE
