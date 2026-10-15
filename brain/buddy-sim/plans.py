"""Per-block plan for chapters 25-28, keyed by the block's opening-fence line in the chapter file (repo b58f557).
('run', {opts})                 verbatim block; opts: stdin, timeout, check_cmd, script (+why) = translated text
('file', dest, {write, repo, live, piece_of})
('snippet', repo_path[, live_path])
('mac', script, {opts}, translation)
('skip', CLASS, reason[, probe])   probe = a non-guide command whose output informs the skip
'<line>-pre': [('fixup', script, why[, timeout])]   'post': [('extra', script, why[, timeout])]
"""
LH = '/home/burgerbarn'          # the LIVE home on bigbuddy (read-only comparisons)
V = 'voice/buddy_voice.py'

PLAN = {}

# ---------------------------------------------------------------- ch25
PLAN['ch25'] = {
    93: ('run', {'stdin': 'y\n', 'note': 'no -y in the guide: the prompt was answered y on stdin (a person types y)',
                 'check_cmd': 'rpm -q rpmfusion-free-release rpmfusion-nonfree-release; ls /etc/yum.repos.d/ | grep rpmfusion'}),
    122: ('skip', 'GPU', 'NVIDIA driver (akmod build, kernel module, reboot): no GPU in the container by rule'),
    164: ('skip', 'GPU', 'optional NVIDIA container toolkit; also needs the network at run time (curl of the repo file)'),
    193: ('run', {'script': 'sudo dnf swap -y ffmpeg-free ffmpeg --allowerasing\nsudo dnf install -y mpv\nsudo dnf install -y podman\n',
                  'why': '-y added to each line: the guide has no -y, a person answers each prompt with y (one stdin for three prompts is consumed by the first dnf)', 'timeout': 900,
                  'check_cmd': 'rpm -q ffmpeg mpv podman; ffmpeg -hide_banner -encoders 2>/dev/null | grep -E " (libx264|h264_nvenc) "; rpm -q ffmpeg-free'}),
    221: ('file', '/etc/plasmalogin.conf', {'live': '/etc/plasmalogin.conf'}),
    252: ('skip', 'SYSTEMD', 'loginctl enable-linger: no systemd/logind in the container'),
    310: ('run', {'script': 'sudo dnf install -y libatomic\n',
                  'why': 'first line only; the second line (curl https://lmstudio.ai/install.sh | bash) is the LM Studio installer: network at run time and LM Studio, skipped',
                  'check_cmd': 'rpm -q libatomic'}),
    342: ('skip', 'LMSTUDIO', 'lms get downloads 7.6 GB over the network; no LM Studio in the container'),
    402: ('skip', 'LMSTUDIO', 'needs the LM Studio install (lms, ~/.lmstudio/settings.json)',
          "mkdir -p ~/.lmstudio && echo '{\"defaultContextLength\": {\"type\": \"custom\", \"value\": 8192}}' > ~/.lmstudio/settings.json\n"
          "python3 - <<'EOF'\nimport json, pathlib\np = pathlib.Path.home() / \".lmstudio/settings.json\"\ns = json.loads(p.read_text())\n"
          "s[\"defaultContextLength\"] = {\"type\": \"custom\", \"value\": 12192}\np.write_text(json.dumps(s, indent=2) + \"\\n\")\n"
          "print(s[\"defaultContextLength\"])\nEOF\nrm -rf ~/.lmstudio"),
    448: ('file', '~/.config/systemd/user/lmstudio-server.service', {'live': f'{LH}/.config/systemd/user/lmstudio-server.service'}),
    468: ('skip', 'SYSTEMD', 'systemctl --user (no systemd in the container) and lms ps (no LM Studio)',
          'systemd-analyze --user verify ~/.config/systemd/user/lmstudio-server.service; echo verify rc=$?'),
    489: ('skip', 'LMSTUDIO', 'chat requests to LM Studio on :1234 with the model on the GPU'),
    540: ('skip', 'OTHERHOST', 'runs on the robot (never contacted)'),
    565: ('file', '~/wol-suspend-setup/lmstudio-busy-guard.sh', {'live': f'{LH}/wol-suspend-setup/lmstudio-busy-guard.sh'}),
    614: ('file', '~/.config/systemd/user/lmstudio-busy-guard.service', {'live': f'{LH}/.config/systemd/user/lmstudio-busy-guard.service'}),
    628: ('file', '~/wol-suspend-setup/apply.sh', {'live': f'{LH}/wol-suspend-setup/apply.sh'}),
    670: ('file', '~/wol-suspend-setup/revert.sh', {'live': f'{LH}/wol-suspend-setup/revert.sh'}),
    705: ('run', {'script': 'chmod +x ~/wol-suspend-setup/*.sh\nls -l ~/wol-suspend-setup/\nfor f in ~/wol-suspend-setup/*.sh; do bash -n "$f" && echo "syntax ok $f"; done\n',
                  'why': 'chmod line run; apply.sh NOT run (nmcli/NetworkManager, firewalld, KDE kwriteconfig6/powerdevil, systemd --user: none in the container); bash -n syntax check added'}),
    716: ('skip', 'KDE', 'KDE powerdevil + systemctl --user; no desktop in the container',
          'ls -l ~/.config/powerdevilrc 2>&1; command -v kwriteconfig6 kreadconfig6 || echo "no kwriteconfig6/kreadconfig6 (KDE not installed)"'),
    756: ('skip', 'GPU', 'GPU load test against LM Studio + systemd-inhibit/journalctl'),
    781: ('skip', 'OTHERHOST', 'Wake-on-LAN from the laptop to the real bigbuddy (hardware)'),
    'post': [
        ('extra', 'id -u; echo $XDG_RUNTIME_DIR; getent passwd burgerbarn | cut -d: -f7; cat /etc/fedora-release; rpm -q fish 2>&1',
         'Step 1 check (uid) and the login shell a rebuilt bigbuddy would have'),
    ],
}

# ---------------------------------------------------------------- ch26
PLAN['ch26'] = {
    101: ('run', {'timeout': 1800, 'check_cmd':
                  "~/voice/venv/bin/pip list 2>/dev/null | grep -i -E '^(faster|ctranslate|nvidia|openwake|kokoro|onnxruntime|scikit|numpy|scipy|soundfile|requests|websockets|huggingface|phonem|espeak)'"}),
    '173-pre': [('fixup', 'D=$(~/voice/venv/bin/python -c "import openwakeword, os; print(os.path.dirname(openwakeword.__file__))")/resources/models; mkdir -p $D && cp -n /opt/netcache/oww-models/* $D/ && ls $D',
                 'network emulation: the five base model files downloaded at image build by this same call are put where download_models() writes them; the block then runs verbatim')],
    173: ('run', {}),
    196: ('skip', 'GPU', 'Whisper large-v3-turbo on CUDA (GPU not used by rule; weights need the network)',
          'V=~/voice/venv; SP=$($V/bin/python -c "import site;print(site.getsitepackages()[0])"); echo SP=$SP; '
          'ls $SP/nvidia/cublas/lib $SP/nvidia/cudnn/lib 2>&1 | head -30'),
    226: ('run', {'check_cmd': 'rpm -q pulseaudio-utils; ls -l /usr/bin/pactl /usr/bin/parec /usr/bin/paplay'}),
    244: ('run', {'timeout': 900, 'check_cmd': '~/voice/tts-venv/bin/pip list 2>/dev/null | grep -i -E "kokoro|onnxruntime|numpy|phonemizer|espeak"'}),
    '271-pre': [('fixup', 'mkdir -p ~/voice/models/kokoro && cp /opt/netcache/kokoro/* ~/voice/models/kokoro/',
                 'network emulation: the two files were downloaded at image build with the guide\'s exact curl URL; the block\'s `[ -s $f ] ||` guard then skips curl')],
    271: ('run', {}),
    291: ('run', {'timeout': 300}),
    329: ('snippet', 'voice/kokoro_patch.py', f'{LH}/voice/kokoro_patch.py'),
    337: ('snippet', 'voice/kokoro_patch.py', f'{LH}/voice/kokoro_patch.py'),
    349: ('snippet', 'voice/kokoro_patch.py', f'{LH}/voice/kokoro_patch.py'),
    360: ('snippet', 'voice/kokoro_patch.py', f'{LH}/voice/kokoro_patch.py'),
    370: ('file', '~/voice/kokoro_patch.py', {'write': False, 'repo': 'voice/kokoro_patch.py', 'live': f'{LH}/voice/kokoro_patch.py'}),
    416: ('mac', 'cd /repo/voice && cp *.py run_buddy_voice.sh ~/voice/ && cp -r face ~/voice/ && ls ~/voice', {},
          'scp from the laptop emulated with cp from /repo (the laptop repo snapshot, b58f557)'),
    488: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    536: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    558: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    587: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    613: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    649: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    695: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    722: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    735: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    772: ('snippet', 'voice/run_buddy_voice.sh', f'{LH}/voice/run_buddy_voice.sh'),
    782: ('snippet', 'voice/run_buddy_voice.sh', f'{LH}/voice/run_buddy_voice.sh'),
    788: ('snippet', 'voice/run_buddy_voice.sh', f'{LH}/voice/run_buddy_voice.sh'),
    794: ('file', '~/voice/run_buddy_voice.sh', {'write': False, 'repo': 'voice/run_buddy_voice.sh', 'live': f'{LH}/voice/run_buddy_voice.sh'}),
    '821-pre': [('fixup',
                 "cat > /tmp/sim/llm_stub.py <<'EOF'\n"
                 "# emulation of LM Studio's OpenAI API on 127.0.0.1:1234 (no LM Studio/GPU in the container): logs each request, answers a fixed text\n"
                 "import json, http.server\n"
                 "class H(http.server.BaseHTTPRequestHandler):\n"
                 "    def do_POST(self):\n"
                 "        req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))\n"
                 "        open('/tmp/sim/llm_requests.jsonl', 'a').write(json.dumps({k: (len(v) if k in ('tools', 'messages') else v) for k, v in req.items()}) + '\\n')\n"
                 "        b = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Hello, I am Buddy (stub reply from the simulation).'}}]}).encode()\n"
                 "        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)\n"
                 "http.server.HTTPServer(('127.0.0.1', 1234), H).serve_forever()\n"
                 "EOF\n"
                 "setsid nohup python3 /tmp/sim/llm_stub.py > /tmp/sim/llm_stub.log 2>&1 < /dev/null &\nsleep 1; echo stub started",
                 'LM Studio emulation: a stub OpenAI chat endpoint on 127.0.0.1:1234 answering a fixed text (tests Buddy\'s code path, not the model)')],
    821: ('run', {'timeout': 300, 'check_cmd': 'cat /tmp/sim/llm_requests.jsonl'}),
    865: ('file', '~/.config/systemd/user/buddy-voice.service', {'write': False, 'repo': 'voice/buddy-voice.service',
                                                                 'live': f'{LH}/.config/systemd/user/buddy-voice.service'}),
    892: ('mac', 'cp /repo/voice/buddy-voice.service ~/.config/systemd/user/buddy-voice.service && ls -l ~/.config/systemd/user/', {},
          'scp emulated with cp from /repo (no mkdir added: scp would fail the same way if the folder were missing)'),
    901: ('run', {'script': 'mkdir -p ~/.config/systemd/user/buddy-voice.service.d\n'
                            'printf "[Service]\\nEnvironment=BUDDY_ECHO_TAIL_S=1.0\\n" > ~/.config/systemd/user/buddy-voice.service.d/40-echo-tail.conf\n'
                            'printf "[Service]\\nEnvironment=BUDDY_SPEECH_DB=12\\n" > ~/.config/systemd/user/buddy-voice.service.d/50-speech-db.conf\n'
                            'printf "[Service]\\nEnvironment=BUDDY_MIC=robot_mic\\n" > ~/.config/systemd/user/buddy-voice.service.d/60-mic.conf\n',
                  'why': 'the block minus its last line, `systemctl --user daemon-reload` (no systemd)',
                  'check_cmd': 'for f in ~/.config/systemd/user/buddy-voice.service.d/*.conf; do echo "== $f"; cat "$f"; done; '
                               'systemd-analyze --user verify ~/.config/systemd/user/buddy-voice.service 2>&1 | head; echo verify rc=$?'}),
    961: ('mac', 'mkdir -p ~/voice/models/wakeword\ncp /repo/voice/models/yo_buddy_v3.onnx /repo/voice/models/yo_buddy_v2.onnx ~/voice/models/wakeword/\n'
                 'sha256sum ~/voice/models/wakeword/*.onnx; stat -c "%s %n" ~/voice/models/wakeword/*.onnx', {},
          'ssh/scp emulated locally from /repo'),
    1005: ('run', {'check_cmd': 'rpm -q python3.11 python3.11-devel; python3.11 -V'}),
    1015: ('run', {'timeout': 3600, 'check_cmd': '~/voice/oww-train/bin/pip list 2>/dev/null | grep -i -E "^(pyarrow|numpy|datasets) "'}),
    1049: ('mac', 'cp -r /repo/voice/wakeword ~/voice/ && ls ~/voice/wakeword ~/voice/wakeword/kokoro_gen', {}, 'scp -r emulated with cp -r from /repo'),
    1068: ('file', '~/voice/wakeword/prepare_data.py', {'write': False, 'repo': 'voice/wakeword/prepare_data.py', 'live': f'{LH}/voice/wakeword/prepare_data.py'}),
    1157: ('file', '~/voice/wakeword/kokoro_gen/generate_samples.py', {'write': False, 'repo': 'voice/wakeword/kokoro_gen/generate_samples.py', 'live': f'{LH}/voice/wakeword/kokoro_gen/generate_samples.py'}),
    1198: ('file', '~/voice/wakeword/kokoro_gen/kokoro_worker.py', {'write': False, 'repo': 'voice/wakeword/kokoro_gen/kokoro_worker.py', 'live': f'{LH}/voice/wakeword/kokoro_gen/kokoro_worker.py'}),
    1277: ('run', {'timeout': 600}),
    1309: ('file', '~/voice/wakeword/yo_buddy.yaml', {'write': False, 'repo': 'voice/wakeword/yo_buddy.yaml', 'live': f'{LH}/voice/wakeword/yo_buddy.yaml'}),
    1367: ('snippet', 'voice/wakeword/train_wrapper.py', f'{LH}/voice/wakeword/train_wrapper.py'),
    1378: ('snippet', 'voice/wakeword/train_wrapper.py', f'{LH}/voice/wakeword/train_wrapper.py'),
    1387: ('snippet', 'voice/wakeword/train_wrapper.py', f'{LH}/voice/wakeword/train_wrapper.py'),
    1398: ('file', '~/voice/wakeword/train_wrapper.py', {'write': False, 'repo': 'voice/wakeword/train_wrapper.py', 'live': f'{LH}/voice/wakeword/train_wrapper.py'}),
    1436: ('file', '~/voice/wakeword/train.sh', {'write': False, 'repo': 'voice/wakeword/train.sh', 'live': f'{LH}/voice/wakeword/train.sh'}),
    1455: ('skip', 'GPU', 'wake-word training: 17 GB download (network), GPU training, systemd-run --user scope'),
    1541: ('skip', 'GPU', 'packs work/yo_buddy.onnx, which only training (skipped) produces'),
    1566: ('skip', 'AUDIO-HW', 'microphone recording (pw-record on the Komplete Audio 6)'),
    '1583-pre': [('fixup', "mkdir -p ~/voice/wakeword/real && ~/voice/oww-train/bin/python -c \"import numpy as np, soundfile as sf; sr=48000; "
                           "t=np.arange(sr*6)/sr; a=(0.2*np.sin(2*np.pi*220*t)*(np.sin(2*np.pi*0.5*t)>0)+0.01*np.random.randn(len(t))).astype('float32'); "
                           "sf.write('/home/burgerbarn/voice/wakeword/real/owner_neg_1.wav', a, sr, subtype='PCM_16'); print('synthetic owner_neg_1.wav 48 kHz 6 s')\"",
                  'no microphone: a synthetic 48 kHz 6 s owner_neg_1.wav stands in for the recording, so the conversion snippet runs')],
    1583: ('run', {'check_cmd': '~/voice/oww-train/bin/python -c "import soundfile as sf; i=sf.info(\'/home/burgerbarn/voice/wakeword/real/owner_neg_1_16k.wav\'); print(i.samplerate, i.subtype, round(i.duration,2))"'}),
    1605: ('file', '~/voice/wakeword/add_real.py', {'write': False, 'repo': 'voice/wakeword/add_real.py', 'live': f'{LH}/voice/wakeword/add_real.py'}),
    1691: ('file', '~/voice/wakeword/eval_real.py', {'write': False, 'repo': 'voice/wakeword/eval_real.py', 'live': f'{LH}/voice/wakeword/eval_real.py'}),
    '1715-pre': [('fixup', 'mkdir -p ~/voice/wakeword/real/pos_test && ls /tmp/kgen_test/*.wav | head -5 | xargs -I{} cp {} ~/voice/wakeword/real/pos_test/ && ls ~/voice/wakeword/real/pos_test | wc -l\n'
                           'D=$(~/voice/oww-train/bin/python -c "import openwakeword, os; print(os.path.dirname(openwakeword.__file__))")/resources/models; mkdir -p $D && cp -n /opt/netcache/oww-models/* $D/ && ls $D',
                  'no recordings and no retrain: five of the 40 Kokoro clips from the generator test stand in for real/pos_test; yo_buddy_v2.onnx is the repo copy from Option A. The base models are put into the oww-train venv as prepare_data.py ("models", run by train.sh, skipped) would have downloaded them')],
    1715: ('run', {'timeout': 300}),
    1736: ('file', '~/voice/wakeword/record_media_negatives.sh', {'write': False, 'repo': 'voice/wakeword/record_media_negatives.sh', 'live': f'{LH}/voice/wakeword/record_media_negatives.sh'}),
    1765: ('skip', 'AUDIO-HW', 'needs real/neg_media_*.wav recorded from the echo canceller while media plays'),
    1787: ('snippet', 'voice/wakeword/retrain.sh', f'{LH}/voice/wakeword/retrain.sh'),
    1800: ('snippet', 'voice/wakeword/retrain.sh', f'{LH}/voice/wakeword/retrain.sh'),
    1809: ('snippet', 'voice/wakeword/retrain.sh', f'{LH}/voice/wakeword/retrain.sh'),
    1817: ('snippet', 'voice/wakeword/retrain.sh', f'{LH}/voice/wakeword/retrain.sh'),
    1825: ('file', '~/voice/wakeword/retrain.sh', {'write': False, 'repo': 'voice/wakeword/retrain.sh', 'live': f'{LH}/voice/wakeword/retrain.sh'}),
    1851: ('skip', 'GPU', 'retraining (GPU, systemd-run scope)'),
    'post': [
        ('extra', 'sha256sum ~/voice/models/kokoro/* ~/voice/models/wakeword/yo_buddy_v3.onnx; ls ~/.config/systemd/user/ ~/.config/systemd/user/buddy-voice.service.d/',
         'Before you move on: hashes and unit files'),
        ('extra', "cd /tmp && ~/voice/oww-train/bin/python -W ignore -c \"\nimport importlib\nfor m in ['torch','torchaudio','speechbrain','audiomentations','torch_audiomentations','torchinfo','torchmetrics','onnx','onnxruntime','onnxscript','datasets','pyarrow','numpy','scipy']:\n"
                  "    try:\n        x=importlib.import_module(m); print('ok  ', m, getattr(x,'__version__','?'))\n    except Exception as e: print('FAIL', m, type(e).__name__, str(e)[:150])\n"
                  "try:\n    import acoustics; print('acoustics ok')\nexcept Exception as e: print('FAIL acoustics', type(e).__name__, str(e)[:150])\n"
                  "for m in ['openwakeword.data','openwakeword.utils']:\n    try:\n        importlib.import_module(m); print(m, 'ok')\n    except Exception as e: print('FAIL', m, type(e).__name__, str(e)[:200])\n\" 2>&1 | grep -v -i warn",
         'the training venv import test the Check box quotes (its command is not in the guide; reconstructed)', 600),
        ('extra', "cd ~/voice/wakeword && ~/voice/oww-train/bin/python -c \"import yaml; c=yaml.safe_load(open('yo_buddy.yaml')); print(sorted(c)); print(c['background_paths'])\"",
         'yo_buddy.yaml parses'),
    ],
}

# ---------------------------------------------------------------- ch27
PLAN['ch27'] = {
    362: ('snippet', 'scripts/ops/audio_levels.sh'),
    370: ('snippet', 'scripts/ops/audio_levels.sh'),
    379: ('snippet', 'scripts/ops/audio_levels.sh'),
    386: ('file', 'scripts/ops/audio_levels.sh (laptop)', {'write': False, 'repo': 'scripts/ops/audio_levels.sh'}),
    415: ('skip', 'OTHERHOST', 'runs on the robot'),
    503: ('snippet', 'buddy_link/robot_api.py'),
    555: ('skip', 'OTHERHOST', 'copies the token from the robot (never contacted)'),
    562: ('skip', 'OTHERHOST', 'streams /audio from the robot (network)'),
    608: ('file', '~/.config/systemd/user/robot-mic.service', {'write': False, 'piece_of': None, 'live': f'{LH}/.config/systemd/user/robot-mic.service'}),
    619: ('file', '~/.config/systemd/user/robot-mic.service', {'write': False, 'live': f'{LH}/.config/systemd/user/robot-mic.service'}),
    626: ('file', '~/.config/systemd/user/robot-mic.service', {'write': False, 'live': f'{LH}/.config/systemd/user/robot-mic.service'}),
    635: ('file', '~/.config/systemd/user/robot-mic.service', {'live': f'{LH}/.config/systemd/user/robot-mic.service'}),
    654: ('skip', 'SYSTEMD', 'systemctl --user daemon-reload / show (no systemd)',
          'systemd-analyze --user verify ~/.config/systemd/user/robot-mic.service; echo verify rc=$?; grep -o "> [^ ;]*fifo" ~/.config/systemd/user/robot-mic.service'),
    698: ('run', {}),
    752: ('mac', 'cp /repo/voice/60-buddy-echo-cancel.conf ~/.config/pipewire/pipewire.conf.d/\n'
                 'cd ~/.config/pipewire/pipewire.conf.d && cp -n 60-buddy-echo-cancel.conf 60-buddy-echo-cancel.conf.bak-20261006-robotmic && ls -l', {},
          'scp + ssh emulated locally from /repo'),
    772: ('snippet', 'voice/60-buddy-echo-cancel.conf', f'{LH}/.config/pipewire/pipewire.conf.d/60-buddy-echo-cancel.conf.bak-20261006-robotmic'),
    791: ('run', {'script': "python3 - <<'EOF'\nimport re, pathlib\n"
                            "p = pathlib.Path.home() / '.config/pipewire/pipewire.conf.d/60-buddy-echo-cancel.conf'\n"
                            "t = p.read_text()\nnew = open('/tmp/sim/blk_text.txt').read() if False else None\n"
                            "blk = '''      capture.props = {\n        node.name = \"xvf_asr_capture\"\n"
                            "        # 2026-10-06: the array is on the robot now; its ASR beam (right channel) arrives as \"robot_mic\"\n"
                            "        # (55-robot-mic.conf). Before: target.object = \"alsa_input.usb-Seeed_Studio_reSpeaker_XVF3800_4-Mic_Array_114993701262800721-00.analog-stereo\", audio.position = [ FR ], stream.dont-remix = true\n"
                            "        target.object = \"robot_mic\"\n        audio.channels = 1\n        audio.position = [ MONO ]\n        node.passive = true\n      }'''\n"
                            "m = re.search(r'      capture\\.props = \\{\\n        node\\.name = \"xvf_asr_capture\".*?\\n      \\}', t, re.S)\n"
                            "print('capture.props block found:', bool(m))\nt2 = t[:m.start()] + blk + t[m.end():]\np.write_text(t2)\n"
                            "print('edited; lines changed:', sum(1 for a, b in zip(t.splitlines(), t2.splitlines()) if a != b), 'old/new line counts', len(t.splitlines()), len(t2.splitlines()))\nEOF\n",
                  'why': 'the manual edit ("Edit the active file so the block reads like this") emulated: the xvf_asr_capture capture.props block is replaced by the guide text'}),
    805: ('run', {}),
    811: ('run', {'timeout': 120, 'script':
                  '# EMULATION of: systemctl --user daemon-reload; systemctl --user restart pipewire pipewire-pulse wireplumber; sleep 3;\n'
                  '#               systemctl --user enable --now robot-mic.service   (no systemd in the container)\n'
                  'export XDG_RUNTIME_DIR=/run/user/$(id -u)\n'
                  'pkill -x pipewire; pkill -x pipewire-pulse; pkill -x wireplumber; pkill -f sim_tone.py; sleep 1\n'
                  '[ -S $XDG_RUNTIME_DIR/bus ] || dbus-daemon --session --fork --address=unix:path=$XDG_RUNTIME_DIR/bus\n'
                  'export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus\n'
                  'setsid nohup pipewire > /tmp/sim/pipewire.log 2>&1 < /dev/null &\nsleep 1\n'
                  'setsid nohup pipewire-pulse > /tmp/sim/pipewire-pulse.log 2>&1 < /dev/null &\n'
                  'setsid nohup wireplumber > /tmp/sim/wireplumber.log 2>&1 < /dev/null &\n'
                  'sleep 3\n'
                  '# robot-mic.service ExecStartPre (minus the token header: no robot) and ExecStart with a 440 Hz tone in place of curl /audio\n'
                  '[ -p $XDG_RUNTIME_DIR/robot_mic.fifo ] && echo "fifo already there (created by PipeWire)" || { echo "mkfifo by the unit"; mkfifo $XDG_RUNTIME_DIR/robot_mic.fifo; }\n'
                  "cat > /tmp/sim/sim_tone.py <<'EOF'\nimport math, struct, sys, time\nsr = 16000; n = 0\nwhile True:\n"
                  "    sys.stdout.buffer.write(b''.join(struct.pack('<h', int(3000 * math.sin(2 * math.pi * 440 * (n + i) / sr))) for i in range(640)))\n"
                  "    sys.stdout.buffer.flush(); n += 640; time.sleep(0.04)\nEOF\n"
                  'setsid nohup bash -c "while true; do python3 /tmp/sim/sim_tone.py > $XDG_RUNTIME_DIR/robot_mic.fifo; sleep 2; done" > /tmp/sim/robot_mic_emul.log 2>&1 < /dev/null &\n'
                  'sleep 2\n'
                  '# --- the block\'s own read-back lines, verbatim:\n'
                  'pactl list short sources | grep -i "robot_mic\\|xvf_asr\\|buddy_aec_source"\n'
                  'pactl info | grep -i "server name"\n'
                  'echo "--- sinks:"; pactl list short sinks\n',
                  'why': 'systemd lines emulated: PipeWire, pipewire-pulse and WirePlumber started by hand on a session D-Bus; robot-mic.service emulated by a writer that feeds a 440 Hz tone into the FIFO (no robot)',
                  'check_cmd': 'grep -i -E "error|fail|warn" /tmp/sim/pipewire.log /tmp/sim/pipewire-pulse.log /tmp/sim/wireplumber.log | head -20'}),
    839: ('run', {'timeout': 60}),
    875: ('file', '~/.config/systemd/user/buddy-voice.service.d/60-mic.conf', {'live': f'{LH}/.config/systemd/user/buddy-voice.service.d/60-mic.conf'}),
    882: ('run', {'script': 'export XDG_RUNTIME_DIR=/run/user/$(id -u)\n'
                            'printf "[Service]\\nEnvironment=BUDDY_MIC=robot_mic\\n" > ~/.config/systemd/user/buddy-voice.service.d/60-mic.conf\n'
                            'pactl set-source-volume robot_mic 0dB\n',
                  'why': 'first three lines; the systemctl restart / journalctl lines need systemd and Buddy on the GPU (skipped)',
                  'check_cmd': 'pactl get-source-volume robot_mic'}),
    902: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    936: ('snippet', V, f'{LH}/voice/buddy_voice.py'),
    966: ('snippet', 'buddy_link/robot_api.py'),
    976: ('snippet', 'buddy_link/robot_api.py'),
    1014: ('skip', 'OTHERHOST', 'posts to the robot API /play (never contacted); also needs ~/.config/rosorin/api_token'),
    1059: ('file', 'scripts/ops/buddy_ears.sh (laptop)', {'write': False, 'repo': 'scripts/ops/buddy_ears.sh'}),
    1076: ('skip', 'SYSTEMD', 'buddy_ears.sh restarts the audio stack and Buddy with systemctl over ssh on the live bigbuddy (never run against the live machine)'),
    1105: ('run', {'timeout': 60}),
    'post': [
        ('extra', 'export XDG_RUNTIME_DIR=/run/user/$(id -u); pkill -f "sim_tone.py"; pkill -f "robot_mic.fifo; sleep 2"; sleep 1; '
                  'timeout 4 parec -d robot_mic --raw --format=s16le --rate=16000 --channels=1 2>/dev/null | python3 -c "\n'
                  "import sys,array,math; b=sys.stdin.buffer.read(); a=array.array('h',b[:len(b)//2*2]); \n"
                  "print('robot_mic bytes', len(b), 'rms', round(math.sqrt(sum(x*x for x in a)/max(1,len(a))),1) if a else 'NO DATA')\"; "
                  'pactl list short sources | grep -i "robot_mic\\|xvf_asr\\|buddy_aec_source"',
         'the measurement with the writer stopped (robot-mic.service down): what the guide says to expect (rms 0.0)'),
        ('extra', 'export XDG_RUNTIME_DIR=/run/user/$(id -u); pactl list short modules | grep -i -E "pipe|echo|loopback|null"; pactl list short sinks',
         'which PipeWire modules loaded from the two conf files'),
    ],
}

# ---------------------------------------------------------------- ch28
PLAN['ch28'] = {
    76: ('run', {'timeout': 900}),
    92: ('file', '/etc/plasmalogin.conf', {'live': '/etc/plasmalogin.conf'}),
    134: ('snippet', 'voice/face_server.py', f'{LH}/voice/face_server.py'),
    148: ('snippet', 'voice/face_server.py', f'{LH}/voice/face_server.py'),
    178: ('mac', 'R=/repo/voice; cp $R/face_server.py $R/buddy_voice.py $R/media_screen.py ~/voice/ && cp -r $R/face ~/voice/ && echo copied', {},
          'first line emulated with cp from /repo; second line (ssh bigbuddy systemctl --user restart buddy-voice.service) needs systemd: skipped'),
    '183-pre': [('fixup', 'cd ~/voice && setsid nohup venv/bin/python -c "import face_server as f, time; f.start(); f.state(\'idle\'); print(\'face up\', flush=True); time.sleep(10**9)" > /tmp/sim/face.log 2>&1 < /dev/null &\nsleep 3; cat /tmp/sim/face.log',
                 'buddy-voice.service emulation for the face only: the same two calls buddy_voice.py makes at start-up (face.start(); face.state(\'idle\')), without Whisper/GPU/microphone')],
    183: ('run', {'timeout': 30}),
    200: ('snippet', 'voice/face_server.py', f'{LH}/voice/face_server.py'),
    211: ('snippet', 'voice/face_server.py', f'{LH}/voice/face_server.py'),
    254: ('snippet', 'voice/media_screen.py', f'{LH}/voice/media_screen.py'),
    298: ('snippet', 'voice/media_screen.py', f'{LH}/voice/media_screen.py'),
    314: ('skip', 'KDE', 'kiosk Chromium needs the Plasma/Wayland session and systemd-run --user'),
    340: ('skip', 'KDE', 'show_face() starts the kiosk through systemd-run --user on Wayland'),
    358: ('file', '~/.config/systemd/user/buddy-face-screen.service', {'write': False, 'repo': 'voice/buddy-face-screen.service', 'live': f'{LH}/.config/systemd/user/buddy-face-screen.service'}),
    377: ('mac', 'cp /repo/voice/buddy-face-screen.service ~/.config/systemd/user/ && ls ~/.config/systemd/user/', {},
          'scp emulated; `systemctl --user daemon-reload && systemctl --user enable buddy-face-screen.service` skipped (no systemd)'),
    388: ('skip', 'SYSTEMD', 'systemctl --user stop/start of the kiosk units (no systemd, no desktop)'),
    434: ('snippet', 'voice/media_screen.py', f'{LH}/voice/media_screen.py'),
    448: ('snippet', 'voice/media_screen.py', f'{LH}/voice/media_screen.py'),
    473: ('mac', "bash -c 'curl -s -o /tmp/sim/mode.out -w \"HTTP %{http_code}\\n\" -X POST --data full http://127.0.0.1:8290/mode; curl -s -o /dev/null -w \"bad mode: HTTP %{http_code}\\n\" -X POST --data nonsense http://127.0.0.1:8290/mode'; tail -5 /tmp/sim/face.log",
          {}, 'ssh bigbuddy emulated locally; -o/-w added to show the status code the prose describes (204, 400 for an unknown mode)'),
    494: ('skip', 'KDE', 'CDP fullscreen fix on the running kiosk (no desktop)'),
    536: ('file', '~/voice/kwin/buddytoggle/metadata.json', {'write': False, 'repo': 'voice/kwin/buddytoggle/metadata.json', 'live': f'{LH}/.local/share/kwin/scripts/buddytoggle/metadata.json'}),
    553: ('file', '~/voice/kwin/buddytoggle/contents/code/main.js', {'write': False, 'repo': 'voice/kwin/buddytoggle/contents/code/main.js', 'live': f'{LH}/.local/share/kwin/scripts/buddytoggle/contents/code/main.js'}),
    571: ('mac', 'mkdir -p ~/voice/kwin ~/.local/share/kwin/scripts; cp -r /repo/voice/kwin/buddytoggle ~/voice/kwin/ && find ~/voice/kwin', {}, 'ssh + scp -r emulated locally'),
    575: ('run', {'script': 'cp -r ~/voice/kwin/buddytoggle ~/.local/share/kwin/scripts/ && find ~/.local/share/kwin/scripts\n'
                            'cp ~/.config/kwinrc ~/.config/kwinrc.bak-buddytoggle\n',
                  'why': 'first two lines; kwriteconfig6 and the gdbus KWin calls need KDE (skipped)'}),
    603: ('skip', 'KDE', 'KWin input devices over D-Bus (touchscreen hardware)'),
    623: ('skip', 'KDE', 'KWin input mapping (touchscreen hardware)'),
    688: ('mac', 'mkdir -p ~/media-apps && tar cf - -C /repo/media jukebox video | tar xf - -C ~/media-apps && find ~/media-apps -type f | sort && find ~/media-apps -type f | wc -l', {},
          'tar pipe emulated with GNU tar from /repo (COPYFILE_DISABLE / --no-xattrs are macOS-specific: not testable here)'),
    709: ('run', {'stdin': 'sim-fake-plex-token\n', 'note': 'a made-up 19-character value typed at the hidden prompt'}),
    722: ('mac', "bash -c 'grep -q \"^\\\\\\${\" ~/media-apps/plex_token && echo \"file holds a \\${...} reference, not a token\" || echo \"not a reference\"'\n"
                 "printf '%s\\n' '${PLEX_TOKEN}' > /tmp/sim/ref_token; bash -c 'grep -q \"^\\\\\\${\" /tmp/sim/ref_token && echo \"file holds a \\${...} reference, not a token\" || echo \"not a reference\"'",
          {}, 'ssh bigbuddy \'...\' emulated as bash -c \'...\' with the same quoted text (the owner\'s remote shell is fish; a rebuilt one is bash); second line: the same test on a file that does hold a ${...} reference'),
    731: ('run', {}),
    746: ('skip', 'PODMAN', 'podman build inside the container (podman-in-container excluded); see the host-side docker build in the results'),
    752: ('file', '~/.config/containers/systemd/buddy-jukebox.container', {'write': False, 'repo': 'media/buddy-jukebox.container', 'live': f'{LH}/.config/containers/systemd/buddy-jukebox.container'}),
    775: ('file', '~/.config/containers/systemd/buddy-video.container', {'write': False, 'repo': 'media/buddy-video.container', 'live': f'{LH}/.config/containers/systemd/buddy-video.container'}),
    799: ('mac', 'mkdir -p ~/.config/containers/systemd && cp /repo/media/buddy-jukebox.container /repo/media/buddy-video.container ~/.config/containers/systemd/ && ls ~/.config/containers/systemd/\n'
                 'echo "--- quadlet generator dry run (not a guide step):"; QUADLET_UNIT_DIRS=~/.config/containers/systemd /usr/libexec/podman/quadlet -dryrun -user 2>&1 | head -60',
          {}, 'ssh + scp emulated locally; a quadlet -dryrun is added to check the generated units'),
    803: ('skip', 'SYSTEMD', 'starting the quadlet units (systemd, podman) and Plex on the server (network)'),
    847: ('run', {'stdin': 'sim-fake-ha-token\n'}),
    854: ('skip', 'NETWORK', 'Home Assistant on the server (network)',
          'cd ~/voice; timeout 60 ~/voice/venv/bin/python home.py list 2>&1 | tail -4; echo rc=$?'),
    882: ('skip', 'NETWORK', 'Evo 150 amplifier on the LAN (network)',
          'cd ~/voice && timeout 60 venv/bin/python evo.py status 2>&1 | tail -3; echo rc=$?'),
    899: ('file', '~/voice/modes.py', {'write': False, 'repo': 'voice/modes.py', 'live': f'{LH}/voice/modes.py'}),
}
