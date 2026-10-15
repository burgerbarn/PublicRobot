"""Robot API for Buddy (roadmap #4: Buddy and the robot are one - the robot is Buddy's body).
Runs ON the robot (~/vision/venv, ROS sourced) as rosorin-api.service. LAN HTTP on :8296; every request needs header
`X-Robot-Token` = contents of ~/.config/rosorin/api_token (created 0600 on first start; copy it to bigbuddy).
Direction is Buddy -> robot only, so bigbuddy opens no ports.

GET  /state         driver state (wheels, e-stop, arm, gamepad mode)
GET  /objects       latest scan: label, bearing, distance, height, score, views, seen_at (?all=1 adds dropped ones)
GET  /frame.jpg     latest camera frame
POST /look          JSON {"label": "a microphone"} | {"bearing_deg": b, "dist_m": d, "height_m": h} | {"pan": p, "wrist": w}
                    -> aims the camera (arm only; refused if wheels enabled or e-stop), returns the frame (JPEG).
POST /task/<mode>   start rosorin-task@<mode> (come | home | sethome): come to the owner, go home, save this spot as home
GET  /task          {active, last}: any rosorin-task@ running + last line of the newest ~/explore/<run>/log.jsonl
POST /stop          stop rosorin-task@* and rosorin-explore now (their exit disables the wheels)
GET  /roomscan      latest room look-around {t, dir, shots: [view, pan, wrist, dets]}; /roomscan.jpg = 3x2 mosaic
GET  /stream.mjpg   live camera, MJPEG at the camera rate (only while a client is connected)
POST /play          the robot's mouth: a WAV body is played on the reSpeaker's speaker; returns when it has been played
GET  /audio         the robot's ears: raw s16le mono 16 kHz, the reSpeaker XVF3800's ASR beam (right channel), while a client is connected
GET  /status        panel summary: battery_v, plugged, wheels, estop, arm, mind (+why), task, last run line
GET  /say           the next thing the robot wants said out loud {say: {id, text, priority} | null}; POST /say/ack {id}
GET  /selfcare      latest self-check round (selfcare/self_check.py)
GET  /seen          the mind's latest detections {t, mode, inspecting, dets: [label, score, box]}
POST /heard         JSON {"event": "wake" | "inspect"} | {"event": "speech" | "learn", "text": "..."} from Buddy
                    -> published on /buddy/heard (String JSON + robot receive time) for the mind. No motion here.
Safety: one arm command at a time; the arm/goal publisher exists only during a command (the driver trips on >1
publisher); arm is left disabled/holding afterwards, so auto-tuck takes it home after 2 min idle."""
import glob, json, math, os, secrets, subprocess, threading, time
import sys
sys.path.insert(0, os.path.expanduser('~/selfcare'))        # the robot's self-care: speech queue + latest round
import say as selfcare_say


def selfcare_latest():
    try:
        d = json.load(open(os.path.expanduser('~/selfcare/data/latest.json')))
        return {'time': d.get('time'), 'healthy': d.get('healthy'), 'findings': d.get('findings'),
                'evidence': [{k: e[k] for k in ('id', 'check', 'status', 'text')} for e in d.get('evidence', [])]}
    except (OSError, ValueError):
        return {'time': None}
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import BatteryState, Image, JointState
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger

import sys
sys.path.insert(0, os.path.expanduser('~/vision'))
from mind_pause import mind_paused                     # the robot mind hands the arm over for a look

PORT = int(os.environ.get('ROBOT_API_PORT', '8296'))
AUDIO_LOCK, PLAY_LOCK = threading.Lock(), threading.Lock()
PULSE_ENV = dict(os.environ, XDG_RUNTIME_DIR=os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'),
                 PULSE_SERVER=os.environ.get('PULSE_SERVER', f'unix:/run/user/{os.getuid()}/pulse/native'))
TOKEN_FILE = os.path.expanduser('~/.config/rosorin/api_token')
SCANS = os.path.expanduser('~/vision/scans')
DRV = '/rosorin_board_driver'
HOME_PAN, WRIST_DEFAULT, CAM_Z = 500, 178, 0.33


SPEECH_TARGET_DBFS = float(os.environ.get('ROBOT_SPEECH_TARGET_DBFS', '-6'))    # loudness target (RMS of the loud parts)
SPEECH_MAX_BOOST_DB = float(os.environ.get('ROBOT_SPEECH_MAX_BOOST_DB', '24'))
TONE_PEAK_DBFS = float(os.environ.get('ROBOT_TONE_PEAK_DBFS', '-20'))         # short clips (Buddy's chime / cue): peak level, owner 2026-10-06 "WAY down"
SPEECH_GAIN_DB = float(os.environ.get('ROBOT_SPEECH_GAIN_DB', '12'))            # fixed gain stage before the limiter (owner: "high gain")
SPEECH_OUT_DB = float(os.environ.get('ROBOT_SPEECH_OUT_DB', '-2.75'))          # after the limiter (owner 2026-10-07: voice down 10 % = PulseAudio's 90 %)


def normalize_wav(wav):
    """What an EasyEffects chain would do, in 30 lines, headless (owner 2026-10-06): the reSpeaker's small amp is the
    ceiling, so the clip is made as loud as it can be without clipping - peak to -1 dBFS, then a slow compressor that
    lifts the quieter parts of speech toward SPEECH_TARGET_DBFS (up to SPEECH_MAX_BOOST_DB), then a hard ceiling at
    -1 dBFS. Returns (wav, gain applied in dB to the loud parts). Tones/chimes pass through with peak normalization."""
    import io, wave
    try:
        w = wave.open(io.BytesIO(wav)); params = w.getparams(); frames = w.readframes(w.getnframes()); w.close()
        if params.sampwidth != 2:
            return wav, 0.0
        a = np.frombuffer(frames, np.int16).astype(np.float32) / 32768.0
        if len(a) < params.framerate // 10 or float(np.abs(a).max()) == 0.0:
            return wav, 0.0
        if len(a) < params.framerate * params.nchannels * 1.2:   # a chime or cue, not speech: set its peak and stop
            a = a * (10 ** (TONE_PEAK_DBFS / 20) / float(np.abs(a).max()))
            out = io.BytesIO(); w2 = wave.open(out, 'wb'); w2.setparams(params)
            w2.writeframes((a * 32767).astype(np.int16).tobytes()); w2.close()
            return out.getvalue(), TONE_PEAK_DBFS
        ceiling = 10 ** (-1 / 20)
        a = a * (ceiling / float(np.abs(a).max()))          # 1. peak at -1 dBFS
        hop = max(1, params.framerate * params.nchannels // 50)   # 2. 20 ms frames: level of the loud parts
        n = len(a) // hop * hop
        rms = np.sqrt((a[:n].reshape(-1, hop) ** 2).mean(1) + 1e-12)
        loud = float(np.percentile(rms[rms > 0.01], 80)) if (rms > 0.01).any() else float(rms.max())
        want = 10 ** (SPEECH_TARGET_DBFS / 20)
        boost = min(10 ** (SPEECH_MAX_BOOST_DB / 20), max(1.0, want / loud))
        # per-frame gain: full boost where the frame is quiet, less where it would exceed the ceiling; smoothed
        g = np.minimum(boost, ceiling / np.maximum(rms * 3.0, 1e-6))      # 3 x RMS ~ speech peaks
        g = np.convolve(np.r_[g[0], g, g[-1]], np.ones(5) / 5, mode='same')[1:-1]
        gain = np.repeat(np.maximum(1.0, g), hop)
        gain = np.r_[gain, np.full(len(a) - len(gain), gain[-1])] if len(gain) < len(a) else gain[:len(a)]
        a = a * gain * 10 ** (SPEECH_GAIN_DB / 20)           # 3. the gain stage (owner 2026-10-06: not conservative)
        a = ceiling * np.tanh(a / ceiling)                   # 4. soft limiter: peaks fold into the ceiling, no hard clip
        a = a * 10 ** (SPEECH_OUT_DB / 20)                   # 5. output level (the sink stays at 100 %: it is the ALSA control)
        boost *= 10 ** ((SPEECH_GAIN_DB + SPEECH_OUT_DB) / 20)
        out = io.BytesIO(); w2 = wave.open(out, 'wb'); w2.setparams(params)
        w2.writeframes((a * 32767).astype(np.int16).tobytes()); w2.close()
        return out.getvalue(), round(20 * math.log10(boost), 1)
    except Exception:
        return wav, 0.0


def token():
    if not os.path.exists(TOKEN_FILE):
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_urlsafe(24).encode()); os.close(fd)
    return open(TOKEN_FILE).read().strip()


def aim_for(bearing_deg, dist_m, height_m):
    """(pan, wrist) pointing the camera at a point (measured 2026-09-29: yaw = 1.0 + 0.24 (pan - 500) deg;
    pitch = -10.1 + 0.24 (wrist - 178) deg in the default pose; wrist clamped to the model-checked 150..280)."""
    pan = int(round(HOME_PAN + (bearing_deg - 1.0) / 0.24))
    pitch = math.degrees(math.atan2(height_m - CAM_Z, max(0.1, dist_m)))
    wrist = int(round(WRIST_DEFAULT + (pitch + 10.1) / 0.24))
    return max(34, min(966, pan)), max(150, min(280, wrist))


def latest_scan():
    runs = sorted(glob.glob(os.path.join(SCANS, '*', 'scan.json')))
    if not runs:
        return None, None
    return runs[-1], json.load(open(runs[-1]))


def obj_out(o, seen_at):
    x, y, z = o['xyz']
    return {'label': o['label'], 'bearing_deg': round(math.degrees(math.atan2(y, x)), 1),
            'dist_m': round(math.hypot(x, y), 2), 'height_m': round(z, 2), 'score': o['score'], 'views': o['views'],
            'seen_at': seen_at}


class Robot(Node):
    def __init__(self):
        super().__init__('robot_api')
        self.rgb, self.state = None, {}
        self.create_subscription(Image, '/aurora/rgb/image_raw', lambda m: setattr(self, 'rgb', m), 2)
        self.create_subscription(String, f'{DRV}/state', lambda m: self.state.update(json.loads(m.data)), 5)
        self.cli = {n: self.create_client(T, f'{DRV}/{n}') for n, T in
                    (('arm/enable', SetBool), ('arm/recover', Trigger))}
        self.cmd_lock = threading.Lock()
        self.heard_pub = self.create_publisher(String, '/buddy/heard', 10)
        self.battery_v, self.mind = None, {}                  # for the touchscreen robot panel (GET /status)
        self.create_subscription(BatteryState, '/battery', lambda m: setattr(self, 'battery_v', round(m.voltage, 2)), 5)
        self.create_subscription(String, '/rosorin_mind/mode', lambda m: self.mind.update(json.loads(m.data)),
                                 QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.seen = None                                      # latest /rosorin_mind/seen (mind's detections)
        self.create_subscription(String, '/rosorin_mind/seen', lambda m: setattr(self, 'seen', json.loads(m.data)), 2)

    def wait(self, cond, t, what):
        end = time.time() + t
        while time.time() < end:
            if cond():
                return
            time.sleep(0.03)
        raise TimeoutError(what)

    def call(self, name, req, t=30):
        c = self.cli[name]
        if not c.wait_for_service(timeout_sec=5):
            raise RuntimeError(f'{name} unavailable')
        f = c.call_async(req)
        self.wait(f.done, t, name)
        return f.result()

    def jpeg(self, quality=85):
        m = self.rgb
        if m is None:
            raise RuntimeError('no camera frame (is rosorin-camera running?)')
        img = np.frombuffer(m.data, np.uint8).reshape(m.height, m.width, -1)
        if m.encoding == 'rgb8':
            img = img[:, :, ::-1]
        ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes()

    def look(self, pan, wrist):
        if not self.cmd_lock.acquire(blocking=False):
            raise RuntimeError('busy: another arm command is running')
        try:
            with mind_paused('Buddy look'):
                return self._look(pan, wrist)
        finally:
            self.cmd_lock.release()

    def _look(self, pan, wrist):
        s = self.state
        if s.get('enabled') or s.get('estop'):
            raise RuntimeError(f'refused: wheels enabled or e-stop ({s.get("estop")})')
        if not s.get('arm_holding'):
            self.call('arm/recover', Trigger.Request())
        r = self.call('arm/enable', SetBool.Request(data=True))
        if not r.success:
            raise RuntimeError('arm enable refused: ' + r.message)
        pub = self.create_publisher(JointState, 'arm/goal', 10)
        try:
            self.wait(lambda: pub.get_subscription_count() >= 1, 3, 'driver not subscribed to arm/goal')
            goal = {1: pan, 4: wrist}
            pub.publish(JointState(name=[str(k) for k in goal], position=[float(v) for v in goal.values()]))
            def arrived():
                st = self.state; p = st.get('arm_pose') or {}
                return not st.get('arm_moving') and all(abs(p.get(str(k), -9) - v) <= 1 for k, v in goal.items())
            self.wait(lambda: arrived() or self.state.get('estop'), 20, 'arm move')
            if self.state.get('estop'):
                raise RuntimeError('e-stop during the move')
            t_ok = self.get_clock().now().nanoseconds + 400_000_000         # settle, then a fresh frame
            self.wait(lambda: self.rgb is not None and
                      self.rgb.header.stamp.sec * 1e9 + self.rgb.header.stamp.nanosec > t_ok, 5, 'fresh frame')
            return self.jpeg()
        finally:
            self.destroy_publisher(pub)
            try:
                self.call('arm/enable', SetBool.Request(data=False))
            except Exception:
                pass


def unit_active(u):
    return subprocess.run(['systemctl', 'is-active', '--quiet', u]).returncode == 0


TASKS = ('come', 'home', 'sethome')


def task_active():
    return any(unit_active(f'rosorin-task@{t}') for t in TASKS)


def last_run_line():
    runs = sorted(glob.glob(os.path.expanduser('~/explore/*/log.jsonl')), key=os.path.getmtime)
    if not runs:
        return None
    lines = [l for l in open(runs[-1]).read().splitlines() if '"wheels"' not in l]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def make_handler(robot, tok):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype='application/json', extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            self.send_response(code); self.send_header('Content-Type', ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        def authed(self):
            if secrets.compare_digest(self.headers.get('X-Robot-Token', ''), tok):
                return True
            self.send(401, {'error': 'missing or wrong X-Robot-Token'}); return False

        def do_GET(self):
            if not self.authed():
                return
            path, _, q = self.path.partition('?')
            try:
                if path == '/state':
                    return self.send(200, robot.state)
                if path == '/objects':
                    f, scan = latest_scan()
                    if not scan:
                        return self.send(200, {'scan': None, 'objects': []})
                    seen = os.path.basename(os.path.dirname(f))
                    objs = [obj_out(o, seen) for o in scan['objects']]
                    if 'all=1' in q:
                        objs += [dict(obj_out(o, seen), dropped=True) for o in scan.get('dropped', [])]
                    return self.send(200, {'scan': seen, 'age_s': round(time.time() - os.path.getmtime(f)), 'objects': objs})
                if path == '/status':                             # touchscreen panel: everything at a glance
                    d = robot.state
                    return self.send(200, {'battery_v': robot.battery_v, 'plugged': d.get('plugged'),
                                           'wheels': d.get('enabled'), 'estop': d.get('estop'),
                                           'arm': d.get('arm_enabled'), 'mind': robot.mind.get('mode'),
                                           'mind_why': robot.mind.get('why'), 'task': task_active(),
                                           'last': last_run_line()})
                if path == '/say':                                # what the robot wants said out loud (selfcare/say.py); Buddy polls
                    return self.send(200, {'say': selfcare_say.pending()})
                if path == '/selfcare':                           # latest self-check round
                    return self.send(200, selfcare_latest())
                if path == '/task':
                    return self.send(200, {'active': task_active(), 'last': last_run_line()})
                if path in ('/roomscan', '/roomscan.jpg'):         # latest "look around the room" (mind)
                    dirs = sorted(glob.glob(os.path.expanduser('~/vision/roomscans/*/meta.json')))
                    if not dirs:
                        return self.send(404, {'error': 'no room scan yet'})
                    d = os.path.dirname(dirs[-1])
                    if path == '/roomscan':
                        return self.send(200, dict(json.load(open(dirs[-1])), dir=os.path.basename(d)))
                    return self.send(200, open(os.path.join(d, 'mosaic.jpg'), 'rb').read(), 'image/jpeg')
                if path == '/seen':
                    return self.send(200, robot.seen or {'dets': [], 't': None})
                if path == '/stream.mjpg':                        # live camera at its own rate (~14 fps), panel
                    self.send_response(200)
                    self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                    self.send_header('Cache-Control', 'no-cache'); self.end_headers()
                    last = None
                    try:
                        while True:
                            m = robot.rgb
                            if m is None or m is last:
                                time.sleep(0.01); continue
                            last = m
                            jpg = robot.jpeg(70)
                            self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                             + str(len(jpg)).encode() + b'\r\n\r\n' + jpg + b'\r\n')
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return                                    # panel closed: encoding stops with it
                if path == '/audio':                              # the robot's ears for Buddy (2026-10-06: the array moved
                    return self.stream_audio()                    # from bigbuddy to the robot; bigbuddy pulls this stream)
                if path == '/frame.jpg':
                    return self.send(200, robot.jpeg(), 'image/jpeg')
                self.send(404, {'error': 'unknown path'})
            except Exception as e:
                self.send(500, {'error': str(e)})

        def stream_audio(self):
            """Raw s16le mono 16 kHz: the right USB channel of the reSpeaker XVF3800 (ASR output of the auto-selected
            beam, read from the device 2026-10-05), taken from the robot's own PulseAudio so nothing else loses the
            card. One listener at a time; parec ends when the listener goes."""
            if not AUDIO_LOCK.acquire(blocking=False):
                return self.send(409, {'error': 'another client has the audio stream'})
            p = None
            try:
                src = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True, text=True, env=PULSE_ENV,
                                     timeout=5).stdout
                src = next((l.split('\t')[1] for l in src.splitlines() if 'alsa_input.usb-Seeed' in l), None)
                if src is None:
                    return self.send(503, {'error': 'reSpeaker array not found'})
                p = subprocess.Popen(['parec', f'--device={src}', '--rate=16000', '--channels=2', '--format=s16le',
                                      '--raw', '--latency-msec=40'], stdout=subprocess.PIPE, env=PULSE_ENV)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/L16; rate=16000; channels=1')
                self.send_header('Cache-Control', 'no-cache'); self.end_headers()
                while True:
                    buf = p.stdout.read(2560)                     # 640 stereo frames = 40 ms
                    if not buf:
                        break
                    mono = np.frombuffer(buf[:len(buf) // 4 * 4], np.int16)[1::2].tobytes()
                    self.wfile.write(mono); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            finally:
                if p is not None:
                    p.kill()
                AUDIO_LOCK.release()

        def do_POST(self):
            if not self.authed():
                return
            if self.path.split('?')[0].startswith('/task/'):
                mode = self.path.split('?')[0].split('/')[-1]
                if mode not in TASKS:
                    return self.send(404, {'started': False, 'error': f'unknown task {mode}'})
                if task_active() or unit_active('rosorin-explore'):
                    return self.send(409, {'started': False, 'error': 'already driving'})
                r = subprocess.run(['sudo', 'systemctl', 'start', '--no-block', f'rosorin-task@{mode}'], capture_output=True, text=True)
                return self.send(200 if r.returncode == 0 else 500, {'started': r.returncode == 0, 'error': r.stderr.strip()[:200]})
            if self.path.split('?')[0] == '/stop':
                r = subprocess.run(['sudo', 'systemctl', 'stop', '--no-block', 'rosorin-task@come', 'rosorin-task@home',
                                    'rosorin-task@sethome', 'rosorin-explore'],
                                   capture_output=True, text=True)
                return self.send(200, {'ok': r.returncode == 0, 'error': r.stderr.strip()[:200]})
            if self.path.split('?')[0] == '/play':               # Buddy's voice out of the robot (2026-10-06)
                n = int(self.headers.get('Content-Length', '0'))
                wav = self.rfile.read(n) if n else b''
                if not wav.startswith(b'RIFF'):
                    return self.send(400, {'error': 'WAV body expected'})
                wav, gain_db = normalize_wav(wav)                # the array's output is a headphone-class driver: use all of it
                with PLAY_LOCK:
                    sinks = subprocess.run(['pactl', 'list', 'short', 'sinks'], capture_output=True, text=True,
                                           env=PULSE_ENV, timeout=5).stdout
                    names = [l.split('\t')[1] for l in sinks.splitlines() if '\t' in l]
                    array = next((x for x in names if 'alsa_output.usb-Seeed' in x), None)
                    # the loud speaker (owner 2026-10-06): the kit's WonderEcho box (USB audio, 3 W amp) as speaker only;
                    # the same clip also goes to the reSpeaker array so its echo canceller has the reference
                    loud = next((x for x in names if 'alsa_output.usb-' in x and 'Seeed' not in x), None)
                    outs = [x for x in (loud, array) if x]
                    if not outs:
                        return self.send(503, {'error': 'no speaker found'})
                    procs = [subprocess.Popen(['paplay', f'--device={x}'], stdin=subprocess.PIPE, env=PULSE_ENV) for x in outs]
                    for pr in procs:
                        try:
                            pr.stdin.write(wav); pr.stdin.close()
                        except BrokenPipeError:
                            pass
                    rcs = [pr.wait(timeout=120) for pr in procs]
                return self.send(200, {'played': all(rc == 0 for rc in rcs), 'bytes': n, 'gain_db': gain_db,
                                       'speakers': [x.split('.')[1][:40] for x in outs]})
            if self.path.split('?')[0] == '/say/ack':
                body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 1024)) or b'{}')
                selfcare_say.ack(str(body.get('id', '')))
                return self.send(200, {'ok': True})
            if self.path.split('?')[0] == '/heard':
                try:
                    body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 4096)) or b'{}')
                    ev = {'event': str(body.get('event', ''))[:16], 'text': str(body.get('text', ''))[:500], 't': time.time()}
                    if ev['event'] not in ('wake', 'speech', 'inspect', 'learn', 'scan_room'):
                        return self.send(400, {'error': 'event must be wake or speech'})
                    robot.heard_pub.publish(String(data=json.dumps(ev)))
                    return self.send(200, {'ok': True})
                except ValueError as e:
                    return self.send(400, {'error': f'bad request: {e}'})
            if self.path.split('?')[0] != '/look':
                return self.send(404, {'error': 'unknown path'})
            try:
                body = json.loads(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 4096)) or b'{}')
                if 'label' in body:
                    _, scan = latest_scan()
                    cands = [o for o in (scan or {}).get('objects', []) if o['label'] == body['label']]
                    if not cands:
                        return self.send(404, {'error': f'no {body["label"]} in the latest scan'})
                    o = obj_out(max(cands, key=lambda o: o['score']), '')
                    pan, wrist = aim_for(o['bearing_deg'], o['dist_m'], o['height_m'])
                elif 'bearing_deg' in body:
                    pan, wrist = aim_for(float(body['bearing_deg']), float(body.get('dist_m', 2.0)), float(body.get('height_m', CAM_Z)))
                else:
                    pan, wrist = max(34, min(966, int(body['pan']))), max(121, min(935, int(body.get('wrist', WRIST_DEFAULT))))   # owner limits (arm_limits.yaml)
                img = robot.look(pan, wrist)
                self.send(200, img, 'image/jpeg', {'X-Aim': f'pan={pan};wrist={wrist}'})
            except (KeyError, ValueError) as e:
                self.send(400, {'error': f'bad request: {e}'})
            except Exception as e:
                self.send(409 if 'refused' in str(e) or 'busy' in str(e) else 500, {'error': str(e)})
    return H


def main():
    tok = token()
    rclpy.init()
    robot = Robot()
    ex = SingleThreadedExecutor(); ex.add_node(robot)
    threading.Thread(target=ex.spin, daemon=True).start()
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), make_handler(robot, tok))
    robot.get_logger().info(f'robot API on :{PORT}')
    try:
        srv.serve_forever()
    finally:
        rclpy.shutdown()


if __name__ == '__main__':
    main()
