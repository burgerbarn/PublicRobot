"""Buddy's music, straight to the Evo 150 over USB (owner 2026-10-01: direct and clean - no Chromium, no echo-cancel).
Tracks come from the jukebox server (Plex library); mpv plays them to the Evo's PipeWire sink and is controlled over
its IPC socket. The face mouth taps the Evo sink monitor. User unit: buddy-music (transient).

play(query)    query None = shuffle the whole library; else artist / album / playlist / song (jukebox search order)
play_album()   the current song's album from track 1, then back to shuffling everything
control(a)     pause | resume | next | previous | stop
now_playing()  'Title - Artist (Album)'"""
import json, os, random, socket, subprocess, time

import requests

JUKEBOX = os.environ.get('BUDDY_JUKEBOX', 'http://127.0.0.1:8281')
EVO = os.environ.get('BUDDY_EVO_SINK', 'alsa_output.usb-Cambridge_Audio_Evo_150_00403a6e-00.analog-stereo')
# owner 2026-10-02: through the echo canceller's input (it only READS the music as its reference so Buddy's mic stops
# hearing songs; the audio goes on to the Evo USB sink unchanged). Was straight to EVO: lyrics became 'speech'.
OUT = os.environ.get('BUDDY_MUSIC_SINK', 'buddy_aec_sink')
SOCK = f'/run/user/{os.getuid()}/buddy-mpv.sock'
STATE = os.path.expanduser('~/.cache/buddy-music.json')
UNIT = 'buddy-music'


def _api(path, **params):
    r = requests.get(JUKEBOX + path, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get('tracks', [])


def _shuffled(limit=500):
    t = _api('/api/browse', limit=limit)
    random.shuffle(t)
    return t


def _ipc(*cmd):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(2); s.connect(SOCK)
        s.sendall((json.dumps({'command': list(cmd)}) + '\n').encode())
        return json.loads(s.recv(65536).decode().splitlines()[0])


def active():
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', UNIT]).returncode == 0


def _face(on):
    try:                                                      # same process as buddy_voice: the mouth sings along
        import face_server
        face_server.TAP_SRC = EVO + '.monitor'
        if on or face_server.music_on():
            face_server.music(on, now_playing if on else None)
    except Exception:
        pass


def _start(tracks, loop):
    tracks = [t for t in tracks if t.get('stream_url')]
    if not tracks:
        return False
    m3u = os.path.expanduser('~/.cache/buddy-music.m3u')
    open(m3u, 'w').write('\n'.join(JUKEBOX + t['stream_url'] for t in tracks))
    json.dump({'tracks': tracks}, open(STATE, 'w'))
    subprocess.run(['systemctl', '--user', 'stop', f'{UNIT}.service'], stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', '--user', 'reset-failed', f'{UNIT}.service'], stderr=subprocess.DEVNULL)
    subprocess.run(['systemd-run', '--user', '-q', f'--unit={UNIT}', '--collect', 'mpv', '--no-video', '--really-quiet',
                    f'--volume={level()}',
                    f'--audio-device=pipewire/{OUT}', f'--input-ipc-server={SOCK}', f'--playlist={m3u}']
                   + (['--loop-playlist=inf'] if loop else []), check=True)
    for _ in range(30):                                       # wait for the IPC socket
        if os.path.exists(SOCK):
            break
        time.sleep(0.2)
    _face(True)
    return True


def play(query=None):
    if query:
        tracks = _api('/api/search', q=query)
        if not tracks:
            return f'Nothing in the library matches "{query}".'
        _start(tracks + _shuffled(), loop=False)              # the request first, then shuffle on
        time.sleep(1.5)
        return now_playing()
    _start(_shuffled(), loop=True)
    return 'Shuffling the whole music library.'


def _current():
    try:
        pos = _ipc('get_property', 'playlist-pos').get('data')
        return json.load(open(STATE))['tracks'][pos]
    except Exception:
        return None


def now_playing():
    t = _current() if active() else None
    if not t:
        return 'Nothing is playing.'
    return f"{t['title']} - {t.get('artist') or ''}" + (f" ({t['album']})" if t.get('album') else '')


def play_album():
    t = _current() if active() else None
    if not t or not t.get('album_key'):
        return 'Nothing is playing, so there is no current album.'
    album = _api('/api/album', key=t['album_key'])
    if not album:
        return f'Could not load the album "{t.get("album")}".'
    _start(album + _shuffled(), loop=False)                   # track 1 .. end, then back to shuffle
    return f'Playing the album "{t["album"]}" by {t.get("artist") or "unknown"} from track 1, then back to shuffle.'


VOL_FILE = os.path.expanduser('~/.cache/buddy-music-volume')


def level():
    """Matt's music volume (mpv %, 0-100); what the music returns to after Buddy talks."""
    try:
        return max(0, min(100, int(float(open(VOL_FILE).read()))))
    except (OSError, ValueError):
        return 100


def set_level(v):
    v = max(0, min(100, int(v)))
    open(VOL_FILE, 'w').write(str(v))
    if active():
        _ipc('set_property', 'volume', v)
    return f'Music volume {v} percent.'


def control(action):
    if not active():
        return 'Nothing is playing.'
    if action == 'stop':
        subprocess.run(['systemctl', '--user', 'stop', f'{UNIT}.service'], stderr=subprocess.DEVNULL)
        _face(False)
        return 'Stopped.'
    cmd = {'pause': ('set_property', 'pause', True), 'resume': ('set_property', 'pause', False),
           'next': ('playlist-next', 'force'), 'previous': ('playlist-prev', 'force')}.get(action)
    if not cmd:
        return f'unknown action {action}'
    _ipc(*cmd)
    return {'pause': 'Paused.', 'resume': 'Resuming.', 'next': 'Next track.', 'previous': 'Previous track.'}[action]
