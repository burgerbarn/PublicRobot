"""Buddy's face: tiny local server (stdlib) that serves the face page and streams live state to it.
http://127.0.0.1:8290/face          Buddy's face: a 3D head that turns and tilts (face3d.html, three.js; /face3d = same)
http://127.0.0.1:8290/face.js       drawing code (also used by the corner overlay)
http://127.0.0.1:8290/overlay.js    corner face injected into the jukebox / video pages
http://127.0.0.1:8290/events        Server-Sent Events: {"t":"state",...} and {"t":"bands",...}
Music mode: music(True, caption_fn) -> bands come live from the TV sink monitor (the mouth plays the music).
States: idle | listening | thinking | working (text = tool) | speaking. Bands: 20 values 0..1 (voice spectrum,
~30/s, timed to playback) or a mic level while listening."""
import json, os, queue, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

PORT = int(os.environ.get('BUDDY_FACE_PORT', '8290'))
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'face')
N_BANDS = 20
FPS = 30
DB_FLOOR, DB_RANGE = 70.0, 50.0            # band level -70 dB -> 0, -20 dB -> 1 (calibrated on Kokoro am_onyx)
_clients, _lock = [], threading.Lock()
_last_state = {'t': 'state', 'state': 'idle', 'text': ''}
TAP_SRC = os.environ.get('BUDDY_FACE_TAP', 'buddy_aec_sink.monitor')   # everything Buddy plays to the TV
TAP_SR = 16000
TAP_FLOOR, TAP_RANGE = DB_FLOOR, DB_RANGE     # music window (calibrated below if it differs from speech)
_music = {'t': 'music', 'on': False, 'text': ''}
_tap = {'proc': None, 'caption_fn': None}
listen_req = threading.Event()        # touchscreen tap on the face -> the voice loop listens (like "yo buddy")


def _publish(ev):
    data = ('data: ' + json.dumps(ev) + '\n\n').encode()
    with _lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


def state(name, text=''):
    global _last_state
    changed = name != _last_state.get('state')
    _last_state = {'t': 'state', 'state': name, 'text': text or ''}
    _publish(_last_state)
    if changed and name in ('listening', 'thinking', 'speaking', 'idle'):   # the robot's body language follows
        def go():
            try:
                import requests, robot_eyes
                requests.post(robot_eyes.ROBOT_URL + '/heard', json={'event': 'state', 'text': name},
                              headers=robot_eyes._h(), timeout=1.5)
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()


def level(x):
    """Mic level 0..1 while listening (drives eyes / a small mouth ripple)."""
    _publish({'t': 'level', 'v': round(float(max(0.0, min(1.0, x))), 3)})


def _band_fn(sr, n=1024, floor=DB_FLOOR, rng=DB_RANGE):
    """-> f(segment of n samples) = N_BANDS log-spaced band levels 0..1."""
    edges = np.geomspace(90, 7000, N_BANDS + 1)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    idx = [(np.searchsorted(freqs, lo), max(np.searchsorted(freqs, hi), np.searchsorted(freqs, lo) + 1))
           for lo, hi in zip(edges[:-1], edges[1:])]
    win = np.hanning(n)

    def f(seg):
        mag = np.abs(np.fft.rfft(seg * win)) / (n / 4)                   # amplitude spectrum (Hann gain 0.5)
        db = np.array([20 * np.log10(mag[i:j].mean() + 1e-9) for i, j in idx])
        return np.clip((db + floor) / rng, 0, 1).round(3).tolist()
    return f


def _bands(a, sr):
    """Per-frame log-spaced spectrum of Buddy's voice: list of frames, each N_BANDS values 0..1."""
    hop, n, f = int(sr / FPS), 1024, _band_fn(sr)
    return [f(a[s:s + n]) for s in range(0, max(1, len(a) - n), hop)]


def _tap_loop(proc):
    """Live bands from the TV sink monitor (music + Buddy's voice), ~30/s; caption refreshed every 5 s."""
    hop, n = TAP_SR // FPS, 1024
    f, buf, next_cap = _band_fn(TAP_SR, n, TAP_FLOOR, TAP_RANGE), np.zeros(n, np.float32), 0
    while _tap['proc'] is proc:
        raw = proc.stdout.read(hop * 4)
        if not raw or len(raw) < hop * 4:
            break
        buf = np.concatenate([buf[hop:], np.frombuffer(raw, np.float32)])
        _publish({'t': 'bands', 'b': f(buf)})
        if _tap['caption_fn'] and time.time() > next_cap:
            next_cap = time.time() + 5
            try:
                cap = _tap['caption_fn']() or ''
            except Exception:
                cap = ''
            if cap != _music['text']:
                _music['text'] = cap; _publish(_music)


def music(on, caption_fn=None):
    """Music mode: the mouth is the live spectrum of what plays to the TV (parec tap on the AEC sink monitor)."""
    _music.update(on=bool(on), text=''); _publish(_music)
    old, _tap['proc'] = _tap['proc'], None
    if old:
        old.kill()
    if on:
        _tap['caption_fn'] = caption_fn
        proc = subprocess.Popen(['parec', '-d', TAP_SRC, '--format=float32le', '--channels=1', f'--rate={TAP_SR}',
                                 '--latency-msec=20', '--raw', '--client-name=buddy-face-tap'],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        _tap['proc'] = proc
        threading.Thread(target=_tap_loop, args=(proc,), daemon=True).start()


def music_on():
    return _music['on']


def speak(a, sr, delay=0.05):
    """Stream the spectrum of audio `a` (float, the exact samples about to play) in real time, in a thread."""
    frames = _bands(np.asarray(a, np.float32), sr)
    _publish({'t': 'speak', 'dur': round(len(a) / sr, 2), 'delay': delay})   # caption pages follow the speech
    if _music['on']:                                  # the tap already carries Buddy's voice
        return len(frames) / FPS

    def run():
        t0 = time.time() + delay
        for i, b in enumerate(frames):
            dt = t0 + i / FPS - time.time()
            if dt > 0:
                time.sleep(dt)
            _publish({'t': 'bands', 'b': b})
        _publish({'t': 'bands', 'b': [0] * N_BANDS})
    threading.Thread(target=run, daemon=True).start()
    return len(frames) / FPS


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _file(self, name, ctype):
        try:
            body = open(os.path.join(DIR, name), 'rb').read()
        except OSError:
            self.send_error(404); return
        self.send_response(200); self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*'); self.send_header('Cache-Control', 'no-cache')
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        """POST /mode, body 'desktop' | 'buddy' | 'widget' | 'full': the page buttons (Desktop, Widget/Full) (minimize the kiosk, same KWin call as
        the voice tool / Meta+Shift+B). Server is loopback-only; nothing else is accepted."""
        if self.path.split('?')[0] == '/music':           # face gestures (owner 2026-10-01): swipe / long-press
            act = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 32)).decode(errors='replace').strip()
            try:
                import music
                if act in ('next', 'previous'):
                    msg = music.control(act)
                elif act == 'toggle':
                    paused = music.active() and music._ipc('get_property', 'pause').get('data')
                    msg = music.control('resume' if paused else 'pause')
                elif act in ('vol_up', 'vol_down'):
                    msg = music.set_level(music.level() + (10 if act == 'vol_up' else -10))
                elif act == 'album':
                    msg = music.play_album()
                elif act == 'shuffle':
                    msg = music.play(None)
                elif act == 'playstop':
                    msg = music.control('stop') if music.active() else music.play(None)
                else:
                    return self._json(400, {'error': 'unknown gesture'})
            except Exception as e:
                msg = f'music: {e}'
            return self._json(200, {'msg': msg})
        if self.path.split('?')[0] == '/robot/do':        # robot panel buttons
            act = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 32)).decode(errors='replace').strip()
            path = {'stop': '/stop', 'come': '/task/come', 'home': '/task/home'}.get(act)
            try:
                if act == 'look':
                    import requests, robot_eyes
                    requests.post(robot_eyes.ROBOT_URL + '/heard', json={'event': 'scan_room'}, headers=robot_eyes._h(), timeout=3)
                    return self._json(200, {'msg': 'looking around'})
                if not path:
                    return self._json(400, {'error': 'unknown action'})
                code, body, _ = self._robot('POST', path)
                return self._json(code, body)
            except Exception as e:
                return self._json(502, {'error': str(e)})
        if self.path.split('?')[0] == '/listen':          # tap on the face (touchscreen, owner 2026-10-01)
            info = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 64)).decode(errors='replace').strip()
            print(f'{time.strftime("%H:%M:%S")} touch: {info or "tap"}', flush=True)   # diagnosis of phantom taps
            if info.startswith('ignored'):
                self.send_response(204); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); return
            listen_req.set()
            self.send_response(204); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); return
        if self.path.split('?')[0] != '/mode':
            return self.send_error(404)
        mode = self.rfile.read(min(int(self.headers.get('Content-Length') or 0), 32)).decode(errors='replace').strip()
        if mode not in ('desktop', 'buddy', 'widget', 'full'):
            return self.send_error(400)

        def run():
            try:
                import media_screen
                media_screen.set_mode(mode)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
        self.send_response(204); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()

    def _json(self, code, obj, ctype='application/json'):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers(); self.wfile.write(body)

    def _robot(self, method, path, timeout=5):
        """Relay to the robot API with its token (the token never reaches the page)."""
        import requests, robot_eyes
        r = requests.request(method, robot_eyes.ROBOT_URL + path, headers=robot_eyes._h(), timeout=timeout)
        return r.status_code, r.content, r.headers.get('Content-Type', 'application/json')

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/robot':                                   # touchscreen robot panel (owner 2026-10-01)
            return self._file('robot.html', 'text/html; charset=utf-8')
        if p == '/robot/stream.mjpg':                      # relay the robot's live MJPEG stream as it arrives
            try:
                import requests, robot_eyes
                with requests.get(robot_eyes.ROBOT_URL + '/stream.mjpg', headers=robot_eyes._h(), stream=True,
                                  timeout=(3, 10)) as r:
                    self.send_response(r.status_code)
                    self.send_header('Content-Type', r.headers.get('Content-Type', 'multipart/x-mixed-replace; boundary=frame'))
                    self.send_header('Cache-Control', 'no-cache'); self.end_headers()
                    for chunk in r.iter_content(16384):
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try:
                    self._json(502, {'error': str(e)})
                except Exception:
                    pass
            return
        if p in ('/robot/status', '/robot/frame.jpg'):
            try:
                code, body, ctype = self._robot('GET', '/status' if p == '/robot/status' else '/frame.jpg')
                return self._json(code, body, ctype)
            except Exception as e:
                return self._json(502, {'error': str(e)})
        if p in ('/', '/face', '/face3d'):                  # the 3D face is THE face (owner 2026-09-30; flat page removed)
            return self._file('face3d.html', 'text/html; charset=utf-8')
        if p in ('/face.js', '/overlay.js'):
            return self._file(p[1:], 'application/javascript; charset=utf-8')
        if p == '/vendor/three.module.js':                   # three.js r160, vendored (no internet needed)
            return self._file('vendor/three.module.js', 'application/javascript; charset=utf-8')
        if p == '/events':
            q = queue.Queue(maxsize=200)
            with _lock:
                _clients.append(q)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream'); self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
            try:
                for ev in (_last_state, _music):
                    self.wfile.write(('data: ' + json.dumps(ev) + '\n\n').encode())
                self.wfile.flush()
                while True:
                    try:
                        self.wfile.write(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b': ping\n\n')
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _lock:
                    if q in _clients:
                        _clients.remove(q)
            return
        self.send_error(404)


def start():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), _H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
