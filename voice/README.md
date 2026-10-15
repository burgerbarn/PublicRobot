# Voice stack (bigbuddy)

Compute split and rules: `docs/decisions.md`. Host details: `docs/hardware.md` → bigbuddy voice host.

- `wakeword/` — train the "yo buddy" openWakeWord model on bigbuddy (`train.sh`, unattended).
  Clips are synthesized by Kokoro (`kokoro_gen/`: openWakeWord's generate_samples() interface, work done by
  `kokoro_worker.py` in ~/voice/tts-venv because the training venv needs numpy<2).
  Training venv: ~/voice/oww-train (Python 3.11 from Fedora; torch cu128; openwakeword 0.6.0 --no-deps;
  scipy<1.15 for `acoustics`). Data (~20 GB) in ~/voice/wakeword/data — delete after training.

Run training (protects the desktop):
```
cd ~/voice/wakeword && KOKORO_PROCS=6 KOKORO_THREADS=2 setsid nohup systemd-run --user --scope -q \
  --unit=yo-buddy-train -p MemoryMax=16G -p MemorySwapMax=0 nice -n 19 bash train.sh > train.log 2>&1 < /dev/null &
```

## "yo buddy" model — yo_buddy_v2 (2026-09-28)
`voice/models/yo_buddy_v2.onnx` (215 KB, single file, sha256 5cccf306…; on bigbuddy
~/voice/models/wakeword/). openWakeWord dnn (layer 32, 50k steps, ~50k params), input (1,16,96).
- Data: 20k+2k Kokoro "yo buddy", 20k+2k Kokoro adversarial/sound-alikes, ACAV100M negatives, AudioSet
  background, MIT RIRs; plus owner (AKG P420 → Komplete Audio 6 in1): 18 "yo buddy" clips ×120
  (cardioid) and 66 × 2 s talking chunks ×15 (omni) — `add_real.py`.
- v1 (synthetic only, lost — copy shared the .onnx.data): synthetic test recall 43 % @0.5, 0.09 false/h
  on 10.7 h validation; owner's 23 real clips 6/23 @0.5.
- v2 held-out: owner "yo buddy" 4/5 @0.5 (0.76–0.92, one 0.13); owner talking 22.5 s: 0 triggers (max 0.00);
  robot KA6 file: 2/4.
- Export notes: torch 2.11 ONNX export needs `onnxscript`; it writes external `.onnx.data` → pack with
  onnx.save_model(save_as_external_data=False). TFLite conversion fails (no onnx_tf) — not used.
- Later fine-tuning: more owner clips (omni, varied distance), re-run add_real.py + augment + train.
  Training data (~20 GB) kept on bigbuddy ~/voice/wakeword for that.

## Voice loop — `buddy_voice.py` (live 2026-09-28)
- bigbuddy user service `buddy-voice.service` (file here; installed at ~/.config/systemd/user/), launcher
  `run_buddy_voice.sh` (sets LD_LIBRARY_PATH for the venv's CUDA libs). Runtime venv ~/voice/venv (Py3.14):
  faster-whisper, openwakeword 0.6.0 (--no-deps) + scikit-learn, kokoro-onnx + ~/voice/kokoro_patch.py.
- Loop: parec (P420 via Komplete Audio 6 in1, 16 kHz, 100 Hz HPF) → yo_buddy_v2 @0.5 → chime → record until
  0.8 s silence (speech = > room floor + 6 dB, 6 s start timeout, 12 s max) → Whisper large-v3-turbo (GPU) →
  LM Studio google/gemma-4-12b (reasoning_effort none, last 8 turns) → Kokoro am_onyx → paplay HDMI.
  Half-duplex: mic buffer flushed after each reply.
- Depends on `lmstudio-server.service` (owner's unit, was disabled → enabled 2026-09-28); LM Studio loads the
  model on first request (JIT), unloads after 60 min idle.
- HDMI (Samsung) sink drops the first ~0.3 s after idle: chime and replies are led by 0.4 s silence; replies
  play as one stream.
- Measured live: wake 0.78–0.90; STT 0.1–0.5 s; LLM 0.2–0.5 s; service RAM 2.3 GB; GPU 12.3/16 GB.
- Test without mic: `run_buddy_voice.sh --say "..."`, `--ask "..."`, `--wav file_16k.wav`.
- Web search (owner OK 2026-09-28): Gemma tool `web_search` → SearXNG http://your-searxng-host:8080
  (on server, 192.168.1.122; JSON API enabled). Top 5 results + answers/infobox, ≤2 tool rounds; system prompt
  carries current date/time. LM Studio + gemma-4-12b return native `tool_calls`. Search ~1–2 s.
- Not yet: robot actions (same tool mechanism, via a narrow command interface on the robot).

- Follow-up window: after each answer a soft 660 Hz cue, then BUDDY_FOLLOWUP_S (5 s) listening without wake
  word; chains while answered. One-word/noise follow-ups are ignored silently.
- Echo handling (half-duplex): Mic is read by a background thread with arrival timestamps; after Buddy speaks,
  frames before playback end + BUDDY_ECHO_TAIL_S (0.6 s) are dropped; follow-ups that are >60 % similar to the
  last reply are discarded ("ignored: echo").
- HDMI kept awake: WirePlumber rule `51-hdmi-no-suspend.conf` (here; installed at
  ~/.config/wireplumber/wireplumber.conf.d/) sets session.suspend-timeout-seconds=0 for the HDMI sink (the
  Samsung dropped ~0.3–0.5 s after each idle-suspend). Undo: delete it + `systemctl --user restart wireplumber`.

## Home Assistant (server) — lights + Evo 150 (2026-09-28)
- `home.py`: HA REST at http://192.168.1.122:8123 (IPv6 fallbacks), token ~/voice/ha_token (0600, owner-created
  long-lived token "buddy", entered via hidden prompt). HA runs with Docker Desktop host networking on the Mac:
  its IPv4 forwarding had dropped (only IPv6 answered) — `docker restart homeassistant` fixed it.
- Tools: `lights` (area or light name; on/off/toggle, brightness %) and `light_status` — **light domain only**,
  never switches (the Den area also holds the Evo 150 switches). Areas: den (8 lights), living room, bedroom,
  kitchen, porch. `amp` — allowlisted `media_player.evo_150_2` only: power, input (source_list), volume (≤80 %),
  mute. `tv` — allowlisted `media_player.den_den_tv` ("Den TV", Samsung QN43QN90F, owner set static IP
  192.168.1.110, MAC 74:6d:fa:0a:02:4f; the old `media_player.43_neo_qled` entry is stale): power, input TV/HDMI.
  play_music/play_movie first run `home.ensure_av()`: TV on; the TV wakes the Evo over HDMI-CEC (owner), so it
  waits up to 10 s for that before turning the amp on / to its TV input itself.
  Power-on of the TV not yet tested (needs the TV off first).

## Echo cancellation (2026-09-28)
- PipeWire 1.6.9 module-echo-cancel + WebRTC AEC: `60-buddy-echo-cancel.conf` (here; installed at
  ~/.config/pipewire/pipewire.conf.d/). Virtual sink `buddy_aec_sink` (→ HDMI; the reference) and source
  `buddy_aec_source` (P420 minus playback). Only AEC on (no AGC / noise suppression; HPF on).
- buddy_voice: MIC=buddy_aec_source, SPK=buddy_aec_sink. Kiosk Firefox PULSE_SINK=buddy_aec_sink.
- Measured with Fleetwood Mac playing, speech band (300–3400 Hz), after 3 s convergence: raw −40.6 dBFS →
  echo-cancelled −56.6 dBFS (−16 dB, near the room floor). Owner speech ≈ −39 dBFS.
- Not cancellable: sounds the TV produces from its own sources (not from bigbuddy) → wake-word training with
  TV/music negatives is still the follow-up.

## yo_buddy_v3 (2026-09-28, deployed)
- v2 data + echo-cancelled media recordings (buddy_aec_source, Evo 40 %): negatives music 298 s + 118 s, movies
  2×118 s (+ owner talking) → 549 × 2 s chunks ×15; the same residual (75 % train split, 91 × 10 s) added to the
  augmentation background pool ×10. Owner's music-time "yo buddy" recording discarded (Whisper word timings
  over music unreliable; the residual-background mix covers "yo buddy over music").
- Held-out: Matt 4/5 (0.91, 0.95, 0.30, 0.90, 0.83 vs v2 0.89, 0.92, 0.13, 0.90, 0.76); media/talk 3.1 min 0
  triggers; 10.7 h general audio 0/h (v2 0.09/h); synthetic recall 45.6 % (v2 45.2 %). Rollback: BUDDY_WAKE_MODEL.
- Lesson: training on the GPU while Whisper + LM Studio run → CUDA OOM; `retrain.sh` now pauses buddy-voice
  during the train step and only exports a model that training actually wrote.

## Face (2026-09-28)
- `face_server.py` (stdlib, in the voice process): http://127.0.0.1:8290/face (full-screen face), `/face.js`,
  `/overlay.js`, `/events` (SSE). States idle (amber) / listening (green, mic level) / thinking (cyan) /
  working (violet, tool label) / speaking (warm white). Mouth = spectrum analyzer of Buddy's own voice:
  20 log bands 90–7000 Hz from the exact Kokoro samples, 30 fps timed to playback, mirrored (lows in the middle);
  dB window -70..-20 calibrated on am_onyx (speech-frame band p50/p90/p99 = 0.20/0.77/0.96).
- Idle screen: the kiosk shows the face at start and after "stop" (instead of closing). Over the jukebox /
  video pages `overlay.js` (script tag in both copies) pops a corner face while Buddy is not idle.
- Faceplay (tool `faceplay`, "yo buddy, faceplay <artist>"): the jukebox plays in a background kiosk tab, the
  face stays full screen; `face_server.music()` taps `buddy_aec_sink.monitor` (parec, 16 kHz mono) and the mouth
  shows the live spectrum of what plays (music and Buddy's voice), eyes squint and bob on the bass, caption =
  now playing. Kiosk pref `media.block-autoplay-until-in-foreground=false` (background tabs may play).
  Measured on Fleetwood Mac: band p50/p90/p99 = 0.32/0.61/0.86 with the speech dB window. Plain "play …" still
  shows the jukebox page; "stop" or a movie closes the background tab.
- Bored look (the face): idle, no music, nothing for 5 min (`/face?bored=<s>`) -> heavy lids, slow
  wandering gaze, flat lopsided mouth, a yawn every ~35-75 s; any state change snaps him out of it.
- STT hotwords 'Buddy, faceplay': without them Whisper hears "base play"/"bass play" (Kokoro test clips);
  plain "play …" unaffected.
- Idle moments (face.js `EXPR`): about once a minute (45-75 s) while idle, one of pulse (eyes scale up/down),
  curious (one eye big, head tilt), surprised (big eyes, small o), happy (^ ^), wink, suspicious (lids + side
  glance), look around, double blink; eyes also breathe slowly. `/face?expr=<name>` forces one every 4 s (testing).
- Faceplay is RGB: rainbow mouth (hue by distance from the centre, cycling ~40 deg/s); eyes, glow and caption cycle with the mouth's centre hue. Other states keep their colours.
- Zoom (full-screen face only): idle drifts 0.85-1.15 (37 s + 13 s sines), listening 1.22 (leans in), speaking
  1.0, thinking/working 0.95, faceplay 1.1, bored -0.2. Caption: fixed size, wraps at 94 % of the width; long
  replies page two lines at a time in step with the audio (`{"t":"speak","dur":...}` event).
- Buddy mode / desktop mode: kiosk Firefox runs with `--name buddy-screen` (own Wayland app id). Hotkey
  Meta+Shift+B = KWin script `voice/kwin/buddytoggle` (installed to ~/.local/share/kwin/scripts, kwinrc
  [Plugins] buddytoggleEnabled=true; Meta+B was taken by the power profile). Voice tool `screen_mode`
  ("desktop mode" / "buddy mode") = `media_screen.set_mode()` (one-shot KWin script over D-Bus). Playing music or
  a movie switches back to buddy mode first. Buddy keeps listening in desktop mode.
- Faceplay: no follow-up window while faceplay is on (music resumes right away; wake word only).
- Desktop button (2026-09-29): moving the mouse shows "Desktop ⤓" (hides after 3 s idle) on the face page (top-right)
  and on the jukebox/video pages via overlay.js (top-centre, clear of their titles). Click = POST /mode `desktop` to the
  face server (loopback only; accepts only desktop|buddy) -> media_screen.set_mode (KWin minimize). Verified: POST
  desktop -> window minimized, buddy -> restored, anything else -> 400; screenshots on all three pages.
- Music queries (2026-09-29): play_music/faceplay take artist, album ("Rumours by Fleetwood Mac"), "<name> playlist" or
  song; the tool description lists the jukebox's playlist names at startup. After any tool starts or resumes
  playback (play_music, faceplay, play_movie, media_control resume/next/previous) or when music was paused for the
  wake word, Buddy skips the follow-up window and goes straight back to waiting for "yo buddy".

## 3D face (2026-09-30)
- `/face3d`: the 2D face as a live texture on an invisible cylinder (three.js r160, vendored) - the head turns/tilts
  with gaze and state; captions stay flat. `?quarter=1` = quarter-screen corner, `?body=1` = show the black head.
- Performance on the 4K TV (1920x1080 CSS, dpr 2), same page: Firefox kiosk 3.3 fps (12 after a 1024 texture +
  1080p render); KDE "Web Browser" widget (Qt WebEngine) smooth; **Chromium kiosk 127 fps**. The face drawing itself
  is 1.1 ms/frame - Firefox's WebGL compositing on this Wayland desktop was the bottleneck.
- **The screen is a kiosk CHROMIUM since 2026-09-30** (media_screen.py over CDP :9222, profile ~/.buddy-screen-chromium,
  window class buddy-screen for KWin/Meta+Shift+B). Face, faceplay (face tab + jukebox tab behind), jukebox, movies,
  styles, frame, fullscreen, modes - all verified end to end. The Firefox kiosk + its profile are gone (trash).
- The 3D face is the only face: /face serves face3d.html (flat face.html removed). Desktop widget: `face_widget.sh` (optional).
- **Widget / Full button** (2026-09-30) on the face, jukebox and movie pages (beside Desktop, shows on mouse move):
  POST /mode widget|full -> media_screen.set_mode: KWin takes the kiosk out of full screen into a 25 % window
  bottom-right of the work area, kept above other windows (resize/move: window edges or Alt+drag), and back.
  Verified: 1920x1080 <-> 960x433 page area, label follows the real size.

## Robot eyes — "yo buddy, what do you see?" (2026-10-01)
- `robot_eyes.py`, tools `robot_see` / `teach_robot` in buddy_voice.py. robot_see: robot_api POST /heard inspect
  (mind turns to the owner's hands / held object for 5 s) -> 1.5 s -> /frame.jpg + /seen (robot detections) ->
  Gemma vision answer. Measured 2.5 s end to end.
- Every look saved: ~/rosorin-data/seen/YYYYMMDD/HHMMSS.{jpg,json}; owner confirm/correct -> owner_label + a line in
  ~/rosorin-data/seen/labels.jsonl (the training set). Teaching is immediate: teach_robot -> robot /heard learn -> the robot's detector
  looks for the word from then on; Gemma gets taught names as text only (no example images: light).

## Boot (2026-10-01)
- buddy-voice: user service, linger=yes -> starts at boot (before login). It tries show_face once at start.
- buddy-face-screen.service (here; ~/.config/systemd/user/, WantedBy=graphical-session.target): starts the Chromium
  face when the Plasma session starts (no-op if already up). bigbuddy has no autologin: the face appears after login.

## Touchscreen (2026-10-01)
- WingCool touchscreen = KWin output DP-1 (1920x1080 @ 1.35 -> 1423x800, right of the TV HDMI-A-1).
- Everything lives on DP-1 (owner: face, music, movies; BUDDY_FACE_OUTPUT / BUDDY_MEDIA_OUTPUT, TV = HDMI-A-1);
  stop -> back to DP-1. media_screen._place() = KWin sendClientToScreen + fullScreen.
- Tap the face = listen (like "yo buddy"): face3d.html pointerdown -> face_server POST /listen -> voice loop
  (log "TAP (touchscreen)"). Buttons are excluded. Verified by a real tap 2026-10-01 14:37.
- Touch mapping: KWin had the WingCool touch device (event18) on no output -> taps landed on the TV's coordinates.
  Set via D-Bus org.kde.KWin.InputDevice outputName = DP-1; KDE saved it in ~/.config/kcminputrc (OutputUuid).

## Audio out = Evo 150 over USB (2026-10-01, "buddy only mode")
- Evo 150 on USB (ALSA card E150, sink alsa_output.usb-Cambridge_Audio_Evo_150_00403a6e-00.analog-stereo).
  60-buddy-echo-cancel.conf playback target = that sink (HDMI line kept as a comment; backup of the HDMI version on
  bigbuddy ~/.cache/60-buddy-echo-cancel.conf.hdmi-backup). Evo input = "USB Audio" (home.py amp source usb).
- The TV is out of the audio path; face + media live on the touchscreen (DP-1), so the TV can be powered off.
  bigbuddy-side volume of the Evo USB sink = 100 % (was 40 % = ~-24 dB: too quiet); the Evo is the only volume.
  WirePlumber keeps the per-device volume across restarts.
  Rollback: restore the backup conf, `systemctl --user restart pipewire pipewire-pulse wireplumber`, amp source tv.
- DP-1 (touchscreen) is the primary display (kscreen-doctor output.DP-1.priority.1): the kiosk starts there.
  Moving the running Chromium from the TV (HDR, scale 2) to DP-1 (SDR, 1.35) drew, then went black - start it on the
  right screen instead of moving it.
- Buddy-only mode (owner 2026-10-01): TV output disabled in KDE (kscreen-doctor output.HDMI-A-1.disable); the
  touchscreen is the only display. Back to the TV: `kscreen-doctor output.HDMI-A-1.enable`.

## Music = direct to the Evo over USB (2026-10-01)
- music.py: mpv (user unit buddy-music, IPC /run/user/<uid>/buddy-mpv.sock) plays jukebox-server tracks straight to
  the Evo USB sink - no Chromium, no echo-cancel (owner: direct, clean). play / play_album / control / now_playing;
  the wake word pauses it (duck) and resumes. Faceplay = this player + the face mouth tapping the Evo sink monitor.
- The Chromium jukebox never requested the song (hidden background tab) - no longer in the music path.
- Removed home.ensure_av() before media: it turned the TV on and put the Evo on its TV input at every play.

## Touchscreen controls (2026-10-01)
- Face gestures (face3d.html -> face_server POST /music): tap = listen; swipe left/right = next/previous; swipe
  up/down = music volume +-10; long-press = pause/resume; double-tap = this song's album. A toast confirms.
- ROBOT button (top-left) -> /robot panel (robot.html): live camera, battery / charger / mind / wheels / task,
  STOP, Come here, Go home, Look around, Back to Buddy. face_server relays /robot/* to robot_api with the token
  (robot_api GET /status added: battery, plugged, wheels, estop, arm, mind, task, last run line).
