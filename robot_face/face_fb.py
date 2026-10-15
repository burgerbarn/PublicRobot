"""Buddy's face on the robot's touchscreen, drawn straight into the Linux framebuffer (/dev/fb0) - no X, no browser,
no desktop (owner 2026-10-06: "a self-contained app that runs on top of no GUI", "incredibly simple low-res").
Live state comes from the face server (bigbuddy, face_server.py) as Server-Sent Events:
  {"t":"state","state":"idle|listening|thinking|working|speaking","text":...}   {"t":"bands","b":[20 x 0..1]}
Without the stream it shows the idle face. While the wheels are enabled (the robot is driving) the screen shows what
the robot sees instead - its camera (robot API /frame.jpg) at a few frames a second (owner 2026-10-06: "see what Buddy
sees when he's driving, in lieu of his face, on the local screen"). Service: systemd/rosorin-face.service (user
burgerbarn, group video)."""
import io, json, mmap, os, struct, subprocess, threading, time, wave

import numpy as np
import requests
from PIL import Image

FB = os.environ.get('FACE_FB', '/dev/fb0')
EVENTS = os.environ.get('BUDDY_FACE_EVENTS', 'http://192.168.1.111:8290/events')
FPS = 20
COLORS = {                                  # eye colour per state (B, G, R)
    'idle': (230, 170, 60), 'listening': (90, 220, 90), 'thinking': (40, 170, 240),
    'working': (40, 170, 240), 'speaking': (240, 240, 240), 'offline': (90, 90, 90)}
BG = (12, 10, 8)


def fb_geometry():
    w, h = map(int, open('/sys/class/graphics/fb0/virtual_size').read().split(','))
    bpp = int(open('/sys/class/graphics/fb0/bits_per_pixel').read())
    stride = int(open('/sys/class/graphics/fb0/stride').read())
    return w, h, bpp, stride


SEEN = os.environ.get('ROBOT_SEEN', 'http://127.0.0.1:8296/seen')        # the robot's own eyes (robot API)
STATE = os.environ.get('ROBOT_STATE', 'http://127.0.0.1:8296/state')      # the driver's state: head pan (servo 1)
FRAME = os.environ.get('ROBOT_FRAME', 'http://127.0.0.1:8296/frame.jpg')  # the robot's camera, one JPEG per request
VIEW_FPS = 15                                         # camera view while driving (owner 2026-10-06: 15; measured 45% of one core + API 13%)
FORCE_VIEW = os.environ.get('FACE_VIEW', '') == '1'   # testing: show the camera view without driving
TOUCH = os.environ.get('FACE_TOUCH', '/dev/input/event1')   # the touchscreen (wch.cn USB2IIC_CTP_CONTROL; group input)
PLAY = os.environ.get('ROBOT_PLAY', 'http://127.0.0.1:8296/play')     # the robot's mouth: a WAV body
GIGGLE_S = 1.3                                        # a touch on the screen tickles him (owner 2026-10-06)
MIRROR = os.environ.get('FACE_MIRROR', '1') == '1'   # the screen is on the robot's BACK (owner 2026-10-06): left/right swap
PAN_CENTRE, PAN_PER_DEG = 500, 1 / 0.24               # head pan pulses: 500 = straight ahead, 0.24 deg per pulse
TOKEN_FILE = os.path.expanduser('~/.config/rosorin/api_token')


class Live:
    """The face server's stream, in a thread; state and the latest mouth bands with their age. A second thread asks
    the robot's own eyes where the person is (robot API /seen), so the pupils look at them."""
    def __init__(self):
        self.state, self.text, self.state_t = 'offline', '', 0.0
        self.bands, self.bands_t = np.zeros(20), 0.0
        self.gaze, self.gaze_t = (0.0, 0.0), 0.0                 # where the pupils point: x,y in -1..1 (right, down)
        self.driving, self.view, self.view_t = False, None, 0.0   # wheels enabled; latest camera frame (BGRA, screen size)
        self.size = None                                          # (w, h) of the screen, set by main
        self.touch_t = 0.0                                        # last touch on the screen
        self.mic, self.mic_t = np.zeros(20), 0.0                  # what the robot's own mic hears (owner 2026-10-06)
        threading.Thread(target=self.watch_touch, daemon=True).start()
        threading.Thread(target=self.watch_mic, daemon=True).start()
        threading.Thread(target=self.run, daemon=True).start()
        threading.Thread(target=self.watch_eyes, daemon=True).start()

    def watch_eyes(self):
        try:
            tok = open(TOKEN_FILE).read().strip()
        except OSError:
            return
        while True:
            try:
                h = {'X-Robot-Token': tok}
                st = requests.get(STATE, headers=h, timeout=2).json()
                self.driving = bool(st.get('enabled')) or FORCE_VIEW
                pan = ((st.get('arm_pose') or {}).get('1', PAN_CENTRE) - PAN_CENTRE) / PAN_PER_DEG   # deg, + = left
                sn = requests.get(SEEN, headers=h, timeout=2).json()
                people = [d for d in sn.get('dets', []) if d[0] in ('a face', 'a person')]
                # where the head looks (pan, +-110 deg) plus where the person sits in its picture (+-32 deg half field)
                px = pan / 110.0
                py = 0.0
                if people:
                    d = max(people, key=lambda d: d[1]); b = d[2]
                    px += -((b[0] + b[2]) / 2 - 320) / 320 * 0.3; py = ((b[1] + b[3]) / 2 - 200) / 200 * 0.6
                if MIRROR:
                    px = -px
                self.gaze = (max(-1, min(1, -px)), max(-1, min(1, py)))   # screen x: + = right
                self.gaze_t = time.time()
            except Exception:
                pass
            time.sleep(0.2)

    def watch_mic(self):
        """The robot's mic, live: the array's ASR beam (right channel) from the robot's own PulseAudio, a second
        reader next to the robot API's. 20 log-spaced bands, 100 Hz - 5 kHz, -65..-15 dBFS -> 0..1, 10 times a second.
        Shown as the dim mouth whenever Buddy is not using the mouth himself (owner: "mic input feedback on the face")."""
        env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}', PULSE_SERVER=f'unix:/run/user/{os.getuid()}/pulse/native')
        edges = np.geomspace(100, 5000, 21); n = 1600              # 100 ms at 16 kHz
        f = np.fft.rfftfreq(n, 1 / 16000); idx = [(f >= edges[i]) & (f < edges[i + 1]) for i in range(20)]
        win = np.hanning(n)
        while True:
            p = None
            try:
                src = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True, text=True, env=env,
                                     timeout=5).stdout
                src = next((l.split('\t')[1] for l in src.splitlines() if 'alsa_input.usb-Seeed' in l), None)
                if src is None:
                    time.sleep(3); continue
                p = subprocess.Popen(['parec', f'--device={src}', '--rate=16000', '--channels=2', '--format=s16le', '--raw',
                                      '--latency-msec=50'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
                while True:
                    b = p.stdout.read(n * 4)
                    if len(b) < n * 4:
                        break
                    a = np.frombuffer(b, np.int16).reshape(-1, 2)[:, 1].astype(np.float32) / 32768
                    sp = np.abs(np.fft.rfft(a * win)) / (n / 4)
                    db = np.array([20 * np.log10(np.sqrt(np.mean(sp[m] ** 2)) + 1e-9) for m in idx])
                    self.mic = np.clip((db + 65) / 50, 0, 1) * 0.6 + self.mic * 0.4
                    self.mic_t = time.time()
            except Exception:
                pass
            finally:
                if p:
                    p.kill()
            time.sleep(2)

    def watch_touch(self):
        """A finger on the screen: BTN_TOUCH down from the touchscreen's evdev node -> giggle (face + voice)."""
        try:
            tok = open(TOKEN_FILE).read().strip()
        except OSError:
            tok = None
        fmt = 'llHHi'; n = struct.calcsize(fmt)                   # struct input_event (64-bit timeval)
        while True:
            try:
                with open(TOUCH, 'rb', buffering=0) as f:
                    while True:
                        _, _, typ, code, val = struct.unpack(fmt, f.read(n))
                        if typ == 1 and code == 330 and val == 1 and time.time() - self.touch_t > GIGGLE_S:
                            self.touch_t = time.time()
                            if tok:
                                threading.Thread(target=giggle_sound, args=(tok,), daemon=True).start()
            except Exception:
                time.sleep(2)

    def watch_view(self):
        """While driving: the robot's camera, scaled to fit the screen, as the frame to show (BGRA)."""
        try:
            tok = open(TOKEN_FILE).read().strip()
        except OSError:
            return
        while True:
            if not self.driving or self.size is None:
                time.sleep(0.2); continue
            t = time.time()
            try:
                r = requests.get(FRAME, headers={'X-Robot-Token': tok}, timeout=2)
                im = Image.open(io.BytesIO(r.content)).convert('RGB')
                W, H = self.size; k = min(W / im.width, H / im.height)
                im = im.resize((int(im.width * k), int(im.height * k)), Image.BILINEAR)
                a = np.asarray(im)
                self.view = np.dstack([a[:, :, 2], a[:, :, 1], a[:, :, 0], np.full(a.shape[:2], 255, np.uint8)])
                self.view_t = time.time()
            except Exception:
                pass
            time.sleep(max(0.0, 1.0 / VIEW_FPS - (time.time() - t)))

    def run(self):
        while True:
            try:
                with requests.get(EVENTS, stream=True, timeout=(5, 60)) as r:
                    self.state, self.state_t = 'idle', time.time()
                    for line in r.iter_lines():
                        if not line or not line.startswith(b'data:'):
                            continue
                        ev = json.loads(line[5:])
                        if ev.get('t') == 'state':
                            self.state, self.text, self.state_t = ev.get('state', 'idle'), ev.get('text', ''), time.time()
                        elif ev.get('t') == 'bands':
                            b = np.array(ev.get('b', ev.get('bands', [])), float)   # the server's key is "b"
                            if len(b):
                                self.bands, self.bands_t = np.clip(b, 0, 1), time.time()
            except Exception:
                self.state = 'offline'
                time.sleep(3)


def giggle_wav():
    """Three short falling "hee"s, each a little lower and quieter: 16 kHz mono s16le, ~0.9 s."""
    sr = 16000; out = []
    for i, f0 in enumerate((880, 820, 760)):
        t = np.arange(int(sr * 0.16)) / sr
        f = f0 * (1 - 0.25 * t / t[-1]) * (1 + 0.03 * np.sin(2 * np.pi * 28 * t))   # falls a quarter, light vibrato
        ph = 2 * np.pi * np.cumsum(f) / sr
        y = (np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.15 * np.sin(3 * ph)) * np.hanning(len(t)) * (0.8 ** i)
        out += [y, np.zeros(int(sr * 0.09))]
    y = np.concatenate(out); y = (y / np.abs(y).max() * 0.5 * 32767).astype(np.int16)
    b = io.BytesIO()
    with wave.open(b, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())
    return b.getvalue()


GIGGLE = giggle_wav()


def giggle_sound(tok):
    try:
        requests.post(PLAY, data=GIGGLE, headers={'X-Robot-Token': tok, 'Content-Type': 'audio/wav'}, timeout=5)
    except Exception:
        pass


def disc(img, cx, cy, r, color):
    y, x = np.ogrid[-cy:img.shape[0] - cy, -cx:img.shape[1] - cx]
    img[(x * x + y * y) <= r * r] = color


def main():
    w, h, bpp, stride = fb_geometry()
    assert bpp == 32, bpp
    try:
        open('/sys/class/graphics/fbcon/cursor_blink', 'w').write('0')   # the console cursor would blink through
    except OSError:
        pass
    fd = os.open(FB, os.O_RDWR)
    fb = mmap.mmap(fd, stride * h, mmap.MAP_SHARED, mmap.PROT_WRITE | mmap.PROT_READ)
    live = Live(); live.size = (w, h)
    threading.Thread(target=live.watch_view, daemon=True).start()
    frame = np.zeros((h, stride // 4, 4), np.uint8)
    t0 = time.time(); blink_at = t0 + 3.0; blink_until = 0.0
    eye_r, eye_y0, eye_dx = h // 9, int(h * 0.38), int(w * 0.19)
    eye_y = eye_y0
    mouth_y, mouth_w = int(h * 0.74), int(w * 0.5)
    last_key = None; gaze = (0.0, 0.0); last_draw = 0.0
    while True:
        now = time.time()
        state = live.state if now - live.state_t < 120 or live.state == 'offline' else 'idle'
        col = COLORS.get(state, COLORS['idle'])
        if now >= blink_at:
            blink_until, blink_at = now + 0.12, now + 2.5 + 3.0 * np.random.rand()
        bands = live.bands if now - live.bands_t < 0.5 else np.zeros(20)
        mic = live.mic if now - live.mic_t < 1.0 and now - live.bands_t >= 0.5 else None   # Buddy's own mouth wins
        giggle = now - live.touch_t < GIGGLE_S
        if giggle:                                                # tickled: eyes squeezed, bouncing, mouth laughing
            ph = (now - live.touch_t) * 2 * np.pi * 7
            bands = 0.35 + 0.3 * np.sin(ph + np.linspace(0, np.pi, 20)) * np.hanning(20)
            live.gaze_t = 0.0; gaze = (0.0, 0.0)
        if not giggle and live.driving and live.view is not None and now - live.view_t < 3.0:   # driving: what the robot sees
            key = ('view', live.view_t)
            if key != last_key:
                v = live.view; y0 = (h - v.shape[0]) // 2; x0 = (w - v.shape[1]) // 2
                frame[:, :w] = BG + (255,)
                frame[y0:y0 + v.shape[0], x0:x0 + v.shape[1]] = v
                fb.seek(0); fb.write(frame.tobytes()); last_key, last_draw = key, now
            time.sleep(0.03); continue
        # draw only when something on the face changed (owner 2026-10-06: it cost half a core at 20 fps full redraw)
        # pupils: follow the person the robot sees (held 3 s), else drift slowly back to centre; smoothed
        tx, ty = live.gaze if now - live.gaze_t < 3.0 else (0.0, 0.0)   # the head's direction is always known; 3 s = API silence
        gaze = (gaze[0] + (tx - gaze[0]) * 0.25, gaze[1] + (ty - gaze[1]) * 0.25)
        key = (state, now < blink_until, tuple((bands * 12).astype(int)), round(gaze[0], 2), round(gaze[1], 2),
               None if mic is None else tuple((mic * 10).astype(int)),
               int(now * FPS) if giggle else 0)
        if key == last_key and now - last_draw < 2.0:            # repaint every 2 s anyway: console text may land on the fb
            time.sleep(0.03); continue
        last_key, last_draw = key, now
        frame[:, :w] = BG + (255,)
        # eyes: round, blink every few seconds, narrow while thinking, wide while listening
        openness = 0.12 if now < blink_until else {'thinking': 0.55, 'working': 0.55, 'listening': 1.15}.get(state, 1.0)
        bounce = 0
        if giggle:
            openness = 0.4; bounce = int(eye_r * 0.12 * np.sin((now - live.touch_t) * 2 * np.pi * 7))
        for cx in (w // 2 - eye_dx, w // 2 + eye_dx):
            eye_y = eye_y0 + bounce
            disc(frame, cx, eye_y, eye_r, col + (255,))
            cut = int(eye_r * (1 - openness)) if openness < 1 else 0
            if cut > 0:                                            # lid: flatten the disc from the top
                frame[eye_y - eye_r - 1:eye_y - eye_r + cut + 1, cx - eye_r - 1:cx + eye_r + 2] = BG + (255,)
            px, py = int(cx + gaze[0] * eye_r * 0.45), int(eye_y + gaze[1] * eye_r * 0.35)
            if openness >= 0.5:
                disc(frame, px, py, max(3, int(eye_r * 0.42)), BG + (255,))               # the pupil
                disc(frame, px + eye_r // 6, py - eye_r // 6, max(2, eye_r // 8), col + (255,))   # a glint
        # mouth: 20 bars from the voice spectrum while speaking / the mic level while listening; a line otherwise
        n = len(bands); bw = mouth_w // n; x0 = w // 2 - mouth_w // 2
        dim = tuple(int(c * 0.45) for c in col) + (255,)          # the mic meter: same colour, dimmer
        for i, v in enumerate(bands):
            x = x0 + i * bw
            if mic is not None and not giggle:
                bh = max(3, int(mic[i] * h * 0.18))
                frame[mouth_y - bh // 2:mouth_y + bh // 2, x + 2:x + bw - 2] = dim
                continue
            bh = max(3, int(v * h * 0.18))
            frame[mouth_y - bh // 2:mouth_y + bh // 2, x + 2:x + bw - 2] = col + (255,)
        if state in ('offline',):
            frame[mouth_y - 2:mouth_y + 2, x0:x0 + mouth_w] = col + (255,)
        fb.seek(0); fb.write(frame.tobytes())


if __name__ == '__main__':
    main()
