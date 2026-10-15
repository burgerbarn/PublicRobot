"""Buddy voice loop (bigbuddy). Half-duplex: listen for "yo buddy" -> chime -> record until silence ->
Whisper (GPU) -> Gemma via LM Studio -> Kokoro (am_onyx) on HDMI. Mic is ignored while Buddy speaks.
Conversation only for now (no robot actions). Config: env vars below. Test without mic:
  buddy_voice.py --say "hello"     (TTS only)      buddy_voice.py --ask "what can you do?"  (LLM + TTS)
  buddy_voice.py --wav file.wav     (feed a 16 kHz recording through wake word + STT + LLM + TTS)"""
import argparse, collections, difflib, io, json, os, queue, re, subprocess, sys, threading, time, wave
import numpy as np
import requests
import scipy.signal as ss

E = os.environ.get
try:
    import face_server as face                  # Buddy's face (voice/face_server.py, http://127.0.0.1:8290/face)
except Exception:                               # never let the face break the voice loop
    class face:                                 # noqa: N801 (no-op stand-in)
        state = level = speak = start = staticmethod(lambda *a, **k: None)
        music_on = staticmethod(lambda: False)
# echo-cancelled mic + AEC reference sink (PipeWire module-echo-cancel, voice/60-buddy-echo-cancel.conf);
# the sink forwards to HDMI. Raw devices: Komplete Audio 6 analog-mono-in-a / HDMI hdmi-stereo.
MIC = E('BUDDY_MIC', 'buddy_aec_source')
SPK = E('BUDDY_SPK', 'buddy_aec_sink')
MOUTH = E('BUDDY_MOUTH', 'robot')                   # 'robot': Buddy speaks out of the robot (robot API /play, 2026-10-06);
                                                    # 'evo': through SPK on this machine. Falls back to SPK if the robot does not answer.


def _out(wav_bytes):
    """Buddy's voice: the robot's speaker (reSpeaker XVF3800, whose echo canceller then has its reference), else SPK."""
    if MOUTH == 'robot':
        try:
            import robot_eyes
            r = requests.post(robot_eyes.ROBOT_URL + '/play', data=wav_bytes, headers=robot_eyes._h(),
                              timeout=120)
            if r.ok:
                return
            log(f'robot mouth: {r.status_code} {r.text[:80]} - falling back to {SPK}')
        except Exception as e:
            log(f'robot mouth unreachable ({str(e)[:60]}) - falling back to {SPK}')
    subprocess.run(['paplay', f'--device={SPK}'], input=wav_bytes, check=False)
WAKE_MODEL = E('BUDDY_WAKE_MODEL', os.path.expanduser('~/voice/models/wakeword/yo_buddy_v3.onnx'))
WAKE_TH = float(E('BUDDY_WAKE_TH', '0.5'))
LLM_URL = E('BUDDY_LLM_URL', 'http://localhost:1234/v1/chat/completions')
LLM_MODEL = E('BUDDY_LLM_MODEL', 'google/gemma-4-12b')
VOICE = E('BUDDY_VOICE', 'am_onyx')
KOKORO_DIR = os.path.expanduser('~/voice/models/kokoro')
SPEED_FILE = os.path.expanduser('~/voice/speed')             # Kokoro speaking speed, 0.7-1.5 (owner can say "talk faster")


def speed():
    try:
        return max(0.7, min(1.5, float(open(SPEED_FILE).read())))
    except (OSError, ValueError):
        return float(E('BUDDY_SPEED', '1.0'))


ROBOT_URL = E('BUDDY_ROBOT_URL', 'http://192.168.1.108:8296')                 # robot API (buddy_link/robot_api.py)
ROBOT_TOKEN_FILE = os.path.expanduser('~/.config/rosorin/api_token')


robot_says = []                    # messages fetched from the robot, waiting for the voice loop


def robot_say_poll():
    """Ask the robot whether it wants something said (selfcare/say.py on the robot; GET /say). Never blocks the loop."""
    try:
        tok = open(ROBOT_TOKEN_FILE).read().strip()
        m = requests.get(ROBOT_URL + '/say', headers={'X-Robot-Token': tok}, timeout=2).json().get('say')
        if m and m.get('text') and not any(x['id'] == m['id'] for x in robot_says):
            robot_says.append({'id': m['id'], 'text': str(m['text'])[:300]})
    except Exception:
        pass


def robot_say_ack(mid):
    def go():
        try:
            tok = open(ROBOT_TOKEN_FILE).read().strip()
            requests.post(ROBOT_URL + '/say/ack', json={'id': mid}, headers={'X-Robot-Token': tok}, timeout=2)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def tell_robot(event, text=''):
    """Buddy's ears for the robot (owner 2026-10-01): wake / speech events -> robot /heard. Fire-and-forget in a
    thread: an asleep or unreachable robot never delays the voice loop."""
    def go():
        try:
            tok = open(ROBOT_TOKEN_FILE).read().strip()
            requests.post(ROBOT_URL + '/heard', json={'event': event, 'text': text}, headers={'X-Robot-Token': tok},
                          timeout=1.5)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


SEARCH_URL = E('BUDDY_SEARCH_URL', 'http://your-searxng-host:8080/search')   # SearXNG on server (owner)
SR, FRAME = 16000, 1280                       # openWakeWord: 16 kHz, 80 ms frames
SYSTEM = ("You are Buddy, a small home robot with wheels, a 5-joint arm with a depth camera, and a LiDAR. "
          "You live in the den with Matt, who built you; assume you are talking to Matt and call him Matt "
          "now and then, naturally, not in every reply. "
          "You are talking out loud through a speaker, so answer in one or two short, natural spoken sentences: "
          "no lists, no markdown, no emojis. You have a robot body you can use with tools: robot_see (look "
          "through its camera), robot_come (drive over to Matt and look), robot_go_home, robot_set_home, "
          "robot_stop, teach_robot. Use them when asked; never claim you cannot move or see. "
          "If you did not understand, ask the person to repeat. "
          "For anything current or factual you are unsure of (news, sports, weather, prices, schedules, "
          "opening hours), call web_search first, then answer briefly from the results; say when results are "
          "unclear. Never read out URLs. Do not end with a follow-up question unless you genuinely need more "
          "information to answer. You control the TV screen's media: music from the jukebox (search by artist, "
          "song or album) and movies (by title, or random from a channel: Family, DVD, Blu-Ray, Robotech), with "
          "retro video styles. Use the media tools when asked to play, pause, skip, stop or change the look, and "
          "confirm in a few words. You also control the house lights through Home Assistant (lights only, never "
          "other switches): use the lights tool for on/off/brightness by area or by light name, and light_status "
          "to check. The Evo 150 amplifier (speakers; your own voice, music and movies come in on its 'USB Audio' input - keep it there) is controlled with the "
          "amp tool: power, input, volume (max 80 percent), mute. The Den TV (the screen) has the tv tool: power, "
          "input (TV or HDMI; bigbuddy is on HDMI), status. Playing music or a movie turns the TV and amp on by itself.")

def _tool(name, desc, props=None, required=()):
    return {'type': 'function', 'function': {'name': name, 'description': desc, 'parameters': {
        'type': 'object', 'properties': props or {}, 'required': list(required)}}}


def _playlist_names():
    """Audio playlist names from the jukebox copy (at startup; empty if it isn't up)."""
    try:
        names = requests.get('http://127.0.0.1:8281/api/playlists', timeout=3).json().get('playlists', [])
        return ', '.join(names[:40])
    except Exception:
        return ''


_PL = _playlist_names()
MUSIC_QUERY = ('Default: faceplay, shuffling the entire library - omit query unless Matt names an artist, album, '
               'playlist or song. query = an artist ("Fleetwood Mac"), an album ("Rumours", or "Rumours by Fleetwood Mac"; plays in track '
               'order), a playlist name + " playlist" ("Chill playlist"; plays in order), or a song title; omit to '
               'shuffle everything.' + (f' Playlists: {_PL}.' if _PL else ''))

TOOLS = [
    _tool('robot_look_around', 'Look around the whole room with your robot body\'s camera (arm only) and say what you '
          'see. Use for "what do you see", "look around", "what\'s in the room". Returns what to say.',
          {'question': {'type': 'string', 'description': "Matt's question, verbatim"}}),
    _tool('robot_see', 'Look at what Matt is holding or showing (the camera turns to his hands). Use for "what am I '
          'holding", "what is this", "look at this", or when Matt holds something up. Returns what you see; say it.',
          {'question': {'type': 'string', 'description': "Matt's question, verbatim"}}, ['question']),
    _tool('robot_come', 'Drive your robot body over to Matt and look at what he is showing you. Use for "come check this '
          'out", "come here", "come look at this". It returns at once; you will speak what you see when you arrive.',
          {'question': {'type': 'string', 'description': "what Matt wants to know, e.g. what is this"}}),
    _tool('robot_go_home', 'Drive your robot body back to its home spot. Use for "go home", "go back to your spot".'),
    _tool('robot_set_home', 'Save where your robot body is right now as its home spot (it turns in place once to '
          'find itself on the map). Use for "this is your home", "remember this spot".'),
    _tool('robot_stop', 'Stop your robot body immediately (wheels and any drive). Use for "stop", "halt", "wait".'),
    _tool('teach_robot', 'After robot_see: Matt confirms ("yes", "right") or corrects ("no, that\'s a capo") what the '
          'object is. Saves his label as training data so the robot learns it. label = what it really is.',
          {'label': {'type': 'string'}, 'correct': {'type': 'boolean', 'description': 'true if your answer was right'}},
          ['label']),
    _tool('media_volume', 'Music volume (the music only - your own voice is not affected). Use for plain "turn it up", '
          '"turn the volume down", "volume 60", "louder", "quieter". action up/down = +-10, set = value 0-100.',
          {'action': {'type': 'string', 'enum': ['up', 'down', 'set']}, 'value': {'type': 'integer'}}, ['action']),
    _tool('room_mode', 'Switch the room mode. game = Samsung TV on as the main screen for games (sound stays on the Evo '
          'over USB); appletv = Samsung on the Apple TV, speakers on the TV sound (TV ARC); buddy = Buddy-only: TV '
          'off, the touchscreen is the main screen. Use for "game mode", "apple tv mode", "buddy mode".',
          {'mode': {'type': 'string', 'enum': ['game', 'appletv', 'buddy']}}, ['mode']),
    _tool('voice_speed', 'Change how fast you talk. Use for "talk faster/slower", "speak slower". speed: 0.7 (slow) to '
          '1.5 (fast); normal 1.0; step about 0.1 for "a bit".', {'speed': {'type': 'number'}}, ['speed']),
    _tool('web_search', 'Search the web (private SearXNG) for current information.',
          {'query': {'type': 'string', 'description': 'search query'}}, ['query']),
    _tool('play_music', 'Play music on the TV. ' + MUSIC_QUERY, {'query': {'type': 'string'}}),
    _tool('faceplay', 'Faceplay: play music through your own face - your face stays full screen on the TV and your '
          'mouth is the visualizer. Use when the user says faceplay (may be heard as "face play", "base play" or "bass play"). '
          + MUSIC_QUERY, {'query': {'type': 'string'}}),
    _tool('screen_mode', 'Switch the TV between buddy mode (your face full screen) and desktop mode (the computer '
          'desktop; you keep listening). Use for "desktop mode", "buddy mode", "show the desktop", "show your face".',
          {'mode': {'type': 'string', 'enum': ['buddy', 'desktop']}}, ['mode']),
    _tool('play_album', 'Play the album of the song playing right now, from track 1; afterwards music goes back to '
          'shuffling the whole library. Use for "play album", "play this album", "play the whole album".'),
    _tool('play_movie', 'Play a movie on the TV by title, or a random one (optionally from a channel).',
          {'title': {'type': 'string'}, 'channel': {'type': 'string', 'enum': ['Family', 'DVD', 'Blu-Ray', 'Robotech']}}),
    _tool('media_control', 'Control what is playing on the TV.',
          {'action': {'type': 'string', 'enum': ['pause', 'resume', 'next', 'previous', 'stop']}}, ['action']),
    _tool('set_video_style', 'Change the retro look of the playing movie; strength in percent (default per style, '
          'max 150) for "stronger"/"subtler".',
          {'style': {'type': 'string', 'enum': ['crt', 'vhs', '8mm', 'security cam', 'gameboy', 'ascii', 'off']},
           'strength': {'type': 'integer'}}, ['style']),
    _tool('fullscreen', 'Go fullscreen or back to the normal view (movie: picture fills the screen; music: jukebox LCD '
          'fullscreen). Media starts in the normal view.', {'on': {'type': 'boolean'}}, ['on']),
    _tool('tv_frame', 'Show or hide the retro TV-set frame around the movie (hidden by default).',
          {'on': {'type': 'boolean'}}, ['on']),
    _tool('now_playing', 'What song is playing on the TV right now.'),
    _tool('lights', 'Turn house lights on/off or set brightness. target = an area or a light name.',
          {'target': {'type': 'string', 'description': 'area (den, living room, bedroom, kitchen, porch) or light name'},
           'action': {'type': 'string', 'enum': ['on', 'off', 'toggle']},
           'brightness': {'type': 'integer', 'description': 'percent 1-100, only with on'}}, ['target', 'action']),
    _tool('light_status', 'Are the lights of an area (or one light) on?',
          {'target': {'type': 'string'}}, ['target']),
    _tool('tv', 'Control the Den TV (Samsung): power, input, status. Volume goes through the amp tool.',
          {'action': {'type': 'string', 'enum': ['status', 'on', 'off', 'source']},
           'value': {'type': 'string', 'enum': ['TV', 'HDMI']}}, ['action']),
    _tool('amp', 'Control the Evo 150 amplifier / speakers (master volume for everything, incl. your voice). Only when '
          'Matt says amp, speakers or Evo; plain "volume up/down" is media_volume.',
          {'action': {'type': 'string', 'enum': ['status', 'on', 'off', 'source', 'volume', 'volume_up', 'volume_down',
                                                  'mute', 'unmute']},
           'value': {'type': 'string', 'description': 'input name for source (e.g. TV, Turntable, Internet Radio), '
                                                      'or volume percent 0-80'}}, ['action']),
]
MEDIA_TOOLS = {'play_album', 'play_music', 'faceplay', 'play_movie', 'media_control', 'set_video_style', 'tv_frame', 'fullscreen'}


TOOL_LABELS = {'room_mode': 'switching modes', 'robot_look_around': 'looking around', 'robot_go_home': 'going home', 'robot_set_home': 'remembering this spot', 'robot_come': 'coming over', 'robot_stop': 'stopping', 'robot_see': 'looking', 'teach_robot': 'learning', 'web_search': 'searching the web', 'play_music': 'finding music', 'screen_mode': 'switching the screen', 'faceplay': 'finding music', 'play_movie': 'finding a movie',
               'media_control': 'on it', 'set_video_style': 'changing the look', 'fullscreen': 'on it',
               'tv_frame': 'on it', 'now_playing': 'checking', 'lights': 'lights', 'light_status': 'checking the lights',
               'tv': 'TV', 'amp': 'speakers'}


GENERIC_MUSIC = re.compile(r'^\s*((some|any|all|random|the|my|whole|entire|everything|anything|music|songs?|tunes|'
                           r'library|shuffle|stuff|something)\s*)+$', re.I)


def _music_query(q):
    """'all music', 'some music', 'random' ... -> None = shuffle the entire library (owner default)."""
    return None if not q or GENERIC_MUSIC.match(q) else q


def keep_usb_input():
    """Evo input back on USB Audio if it drifted (never blocks speech on a Home Assistant hiccup)."""
    try:
        import evo
        r = evo.ensure_usb()
        if r:
            log(f'amp: {r}')
    except Exception as e:
        log(f'amp check skipped: {e}')


def _music_hold(on, prev=None):
    """While Buddy speaks the music goes down to DUCK_VOL (its own volume - Buddy's voice is a separate stream; no
    pause, owner 2026-10-01). on=True returns the previous volume to restore, or None if nothing changed."""
    try:
        import music
        if not music.active():
            return None
        if on:
            v = music._ipc('get_property', 'volume').get('data')
            if v is None or v <= DUCK_VOL:
                return None
            music._ipc('set_property', 'volume', DUCK_VOL); return v
        music._ipc('set_property', 'volume', prev)
    except Exception:
        pass
    return None


def run_tool(name, args):
    face.state('working', TOOL_LABELS.get(name, name))
    if name in ('play_music', 'faceplay', 'play_album', 'play_movie'):
        keep_usb_input()                                       # before the music starts, not after
    import media_screen as ms
    if name in ('robot_see', 'teach_robot', 'robot_come', 'robot_stop', 'robot_go_home', 'robot_set_home',
                'robot_look_around'):
        import robot_eyes
        if name == 'robot_look_around':
            return robot_eyes.robot_look_around(args.get('question', 'what do you see?'))
        if name == 'robot_go_home':
            return robot_eyes.robot_task('home', "I'm home.")
        if name == 'robot_set_home':
            return robot_eyes.robot_task('sethome', 'Got it, this is my home spot now.')
        if name == 'robot_come':
            return robot_eyes.robot_come(args.get('question', 'what is this?'))
        if name == 'robot_stop':
            return robot_eyes.robot_stop()
        if name == 'robot_see':
            return robot_eyes.robot_see(args.get('question', 'what do you see?'))
        return robot_eyes.teach_robot(args.get('label', ''), bool(args.get('correct')))
    if name == 'media_volume':
        import music
        a = args.get('action', 'up')
        v = args.get('value') if a == 'set' else music.level() + (10 if a == 'up' else -10)
        return music.set_level(v if v is not None else music.level())
    if name == 'room_mode':
        import modes
        return modes.set_mode(args.get('mode', 'buddy'))
    if name == 'voice_speed':
        v = max(0.7, min(1.5, float(args.get('speed', 1.0))))
        open(SPEED_FILE, 'w').write(f'{v:.2f}')
        return f'speaking speed now {v:.2f}'
    if name == 'web_search':
        return web_search(args.get('query', ''))
    # (removed 2026-10-01: home.ensure_av() turned the TV on and switched the Evo to its TV input before every
    #  play - it fought the USB-only setup. Music goes straight to the Evo over USB now: music.py.)
    import music
    if name in ('play_music', 'faceplay'):                    # owner 2026-10-01: direct to the Evo USB, faceplay
        return music.play(_music_query(args.get('query')))
    if name == 'screen_mode':
        return ms.set_mode(args.get('mode', 'buddy'))
    if name == 'play_album':
        return music.play_album()
    if name == 'play_movie':
        return ms.play_movie(args.get('title') or None, args.get('channel') or None)
    if name == 'media_control':
        if music.active():
            return music.control(args.get('action', ''))
        return ms.control(args.get('action', ''))
    if name == 'set_video_style':
        return ms.set_style(args.get('style', ''), args.get('strength'))
    if name == 'fullscreen':
        return ms.set_fullscreen(bool(args.get('on')))
    if name == 'tv_frame':
        return ms.set_frame(bool(args.get('on')))
    if name == 'now_playing':
        return music.now_playing() if music.active() else ms.now_playing()
    if name in ('lights', 'light_status'):
        import home
        if name == 'lights':
            return home.set_lights(args.get('target', ''), args.get('action', 'on'), args.get('brightness'))
        return home.status(args.get('target', ''))
    if name == 'amp':                                         # Evo 150: direct StreamMagic API, no Home Assistant
        import evo
        return evo.control(args.get('action', 'status'), args.get('value'))
    if name == 'tv':
        import home
        return home.tv(args.get('action', 'status'), args.get('value'))
    return f'unknown tool {name}'


def web_search(query, n=5):
    r = requests.get(SEARCH_URL, params={'q': query, 'format': 'json'}, timeout=15)
    r.raise_for_status()
    d = r.json()
    out = [f'Answer: {a}' for a in d.get('answers', [])[:2]]
    for ib in d.get('infoboxes', [])[:1]:
        out.append(f"Infobox {ib.get('infobox', '')}: {(ib.get('content') or '')[:400]}")
    for x in d.get('results', [])[:n]:
        date = f" ({x['publishedDate'][:10]})" if x.get('publishedDate') else ''
        out.append(f"- {x.get('title', '')}{date}: {(x.get('content') or '')[:300]}")
    return '\n'.join(out) or 'No results.'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


class Mic:
    """parec raw 16 kHz mono s16 + 100 Hz high-pass, read continuously by a background thread (so nothing queues
    in parec/PipeWire while Buddy speaks). Frames carry arrival time; ignore_until() drops Buddy's own voice by
    time. (A pipe flush left ~seconds of queued speech server-side -> Buddy answered itself, 2026-09-28.)"""
    def __init__(self):
        self.p = subprocess.Popen(['parec', f'--device={MIC}', '--rate=16000', '--channels=1',
                                   '--format=s16le', '--raw', '--latency-msec=40'], stdout=subprocess.PIPE)
        self.sos = ss.butter(4, 100, btype='high', fs=SR, output='sos')
        self.zi = ss.sosfilt_zi(self.sos) * 0.0
        self.q = queue.Queue(maxsize=int(30 * SR / FRAME))     # 30 s cap
        self.skip_until = 0.0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            b = self.p.stdout.read(FRAME * 2)
            if len(b) < FRAME * 2:
                self.q.put((time.time(), None)); return
            x = np.frombuffer(b, np.int16).astype(np.float32)
            y, self.zi = ss.sosfilt(self.sos, x, zi=self.zi)
            item = (time.time(), np.clip(y, -32768, 32767).astype(np.int16))
            try:
                self.q.put_nowait(item)
            except queue.Full:                                 # consumer stalled: drop the oldest frame
                self.q.get_nowait(); self.q.put_nowait(item)

    def frame(self):
        while True:
            t, f = self.q.get()
            if f is None:
                raise RuntimeError('mic stream ended')
            if t >= self.skip_until:
                return f

    def ignore_until(self, t):
        """Drop every frame that arrived before time t (Buddy's own output + TV/HDMI delay)."""
        self.skip_until = t

    def drain(self, seconds):
        for _ in range(int(seconds * SR / FRAME)):
            self.frame()


def db(x):
    """Frame level in dBFS (int16 full scale)."""
    return 20 * np.log10(np.sqrt(np.mean((x.astype(np.float32) / 32768) ** 2)) + 1e-9)


class Voice:
    def __init__(self):
        sys.path.insert(0, os.path.expanduser('~/voice'))
        import kokoro_patch                                   # espeak per-call leak fix (docs/lessons.md)
        kokoro_patch.apply()
        from kokoro_onnx import Kokoro
        self.k = Kokoro(f'{KOKORO_DIR}/kokoro-v1.0.onnx', f'{KOKORO_DIR}/voices-v1.0.bin')

    LEAD_S = 0.4                                   # HDMI sink wakes from suspend and drops the first ~0.3 s

    def render(self, text):
        parts, sr = [], 24000
        for s in [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]:
            a, sr = self.k.create(s, voice=VOICE, speed=speed(), lang='en-us')
            parts += [a, np.zeros(int(0.12 * sr), np.float32)]
        return np.concatenate([np.zeros(int(self.LEAD_S * sr), np.float32)] + parts), sr

    def wav(self, text, rendered=None):
        a, sr = rendered or self.render(text)
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        return buf.getvalue()

    def say(self, text):
        """Whole reply as one stream (lead silence once, short pauses between sentences). The face's mouth gets
        the spectrum of exactly these samples, in time with playback."""
        if text.strip():
            keep_usb_input()
            prev = _music_hold(True)                        # owner 2026-10-01: music low under Buddy's voice
            try:
                return self._say(text)
            finally:
                if prev is not None:
                    _music_hold(False, prev)

    def _say(self, text):
        if text.strip():
            a, sr = self.render(text)
            face.state('speaking', text)
            face.speak(a, sr)
            _out(self.wav(text, (a, sr)))

    def cue(self):
        """Soft single low tone: 'still listening' (follow-up window)."""
        t = np.arange(int(0.12 * 24000)) / 24000
        env = np.minimum(1, np.minimum(t, t[::-1]) / 0.015)
        self._play(np.concatenate([np.zeros(int(0.05 * 24000)), np.sin(2 * np.pi * 660 * t) * env]) * 0.18)

    def _play(self, a, sr=24000):
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype(np.int16).tobytes())
        _out(buf.getvalue())

    def chime(self):
        # HDMI sinks wake from suspend and drop the first ~0.3 s: lead with silence (2026-09-28: chime inaudible)
        t = np.arange(int(0.14 * 24000)) / 24000
        env = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)
        tone = np.concatenate([np.zeros(int(0.4 * 24000)), np.sin(2 * np.pi * 880 * t) * env,
                               np.sin(2 * np.pi * 1320 * t) * env]) * 0.35
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
            w.writeframes((tone * 32767).astype(np.int16).tobytes())
        _out(buf.getvalue())


class Brain:
    def __init__(self):
        self.history = []
        self.used_media = False
        self.started_play = False                      # a tool started a track/movie this turn -> wake word only

    def ask(self, text):
        now = time.strftime('%A %d %B %Y, %H:%M %Z')
        msgs = ([{'role': 'system', 'content': f'{SYSTEM} Current date and time: {now}. Your speaking speed is {speed():.2f}.'}] + self.history[-8:]
                + [{'role': 'user', 'content': text}])
        reply, self.used_media, self.started_play = '', False, False
        for _ in range(3):                                     # at most 2 tool rounds, then answer
            r = requests.post(LLM_URL, json={'model': LLM_MODEL, 'messages': msgs, 'max_tokens': 250,
                                             'temperature': 0.7, 'reasoning_effort': 'none', 'tools': TOOLS},
                              timeout=90)
            r.raise_for_status()
            m = r.json()['choices'][0]['message']
            calls = m.get('tool_calls') or []
            if not calls:
                reply = (m.get('content') or '').strip()
                break
            msgs.append({'role': 'assistant', 'content': m.get('content') or '', 'tool_calls': calls})
            for c in calls:
                name = c['function']['name']
                try:
                    args = json.loads(c['function'].get('arguments') or '{}')
                    log(f'tool {name}: {args}')
                    res = run_tool(name, args)
                    self.used_media |= name in MEDIA_TOOLS
                    self.started_play |= name in ('play_music', 'faceplay', 'play_movie') or (
                        name == 'media_control' and args.get('action') in ('resume', 'next', 'previous'))
                except Exception as e:
                    res = f'{name} failed: {type(e).__name__}: {e}'
                log(f'  -> {str(res)[:120]}')
                msgs.append({'role': 'tool', 'tool_call_id': c['id'], 'content': res})
        self.history += [{'role': 'user', 'content': text}, {'role': 'assistant', 'content': reply}]
        return reply or "Sorry, I lost my train of thought."


DUCK_VOL = float(E('BUDDY_DUCK_VOL', '20'))    # music level (mpv %) while talking to Buddy (owner: music goes low)


def duck_music():
    """Music LOW (not paused) while talking to Buddy (owner 2026-10-01). Returns True if it was playing."""
    try:
        import music
        if music.active() and not music._ipc('get_property', 'pause').get('data'):
            music._ipc('set_property', 'volume', DUCK_VOL); return True
    except Exception:
        pass
    return False


def unduck_music():
    try:
        import music
        if music.active():
            music._ipc('set_property', 'volume', music.level())   # back to Matt's music level
    except Exception:
        pass


def load_stt():
    from faster_whisper import WhisperModel
    return WhisperModel(E('BUDDY_STT_MODEL', 'large-v3-turbo'), device='cuda', compute_type='float16')


# Whisper invents these from noise/silence (video-outro phrases); in the follow-up window they made Buddy answer
# "you're welcome" to nobody (2026-10-01)
PHANTOMS = re.compile(r"^(thank you( (so much|very much|for watching))?|thanks( for watching)?|i'?ll see you next time"
                      r"|see you next time|voil[aà]|bye( bye)?|you|okay|ok|\.+|so|um+|uh+)[.!?]*$", re.I)


def transcribe(stt, audio_i16):
    segs, _ = stt.transcribe(audio_i16.astype(np.float32) / 32768, language='en', beam_size=1,
                             vad_filter=True, condition_on_previous_text=False,   # Silero VAD: noise is never transcribed (owner 2026-10-06)
                             hotwords=None)                  # 2026-10-06: the hotword text came back verbatim on silence ("Buddy, faceplay" -> music nobody asked for)
    segs = [s for s in segs if s.no_speech_prob < 0.6]        # Whisper's own "this was not speech"
    return ' '.join(s.text.strip() for s in segs).strip()


SPEECH_DB = float(E('BUDDY_SPEECH_DB', '6'))   # dB above the room floor that counts as speech
FOLLOWUP_S = float(E('BUDDY_FOLLOWUP_S', '2'))  # owner 2026-10-01: 2 s, music still low  # listen this long for a follow-up without the wake word (0 = off)


def record_command(next_frame, floor_db, start_timeout=6.0):
    """Wait up to start_timeout s for speech, stop after 0.8 s of silence or 12 s total."""
    frames, started, silent, t0, peak = [], False, 0, time.time(), -120.0
    face.state('listening')
    while True:
        f = next_frame()
        lvl = db(f); peak = max(peak, lvl)
        face.level((lvl - floor_db) / 30)
        loud = lvl > floor_db + SPEECH_DB
        if started:
            frames.append(f)
            silent = 0 if loud else silent + 1
            if silent * 0.08 >= 0.8 or len(frames) * 0.08 >= 12:
                log(f'command {len(frames) * 0.08:.1f}s, peak {peak:.1f} dBFS (floor {floor_db:.1f})')
                return np.concatenate(frames)
        elif loud:
            started, frames = True, [f]
        elif time.time() - t0 > start_timeout:
            log(f'no speech: peak {peak:.1f} dBFS, needed > {floor_db + SPEECH_DB:.1f}')
            return None


ECHO_TAIL_S = float(E('BUDDY_ECHO_TAIL_S', '0.6'))   # speaker/TV delay after paplay returns


def echo_of(text, reply):
    """True if a heard follow-up is mostly Buddy's own last reply (echo that slipped through)."""
    a, b = re.sub(r'[^a-z ]', '', text.lower()), re.sub(r'[^a-z ]', '', (reply or '').lower())
    return bool(a and b) and difflib.SequenceMatcher(None, a, b[:len(a) + 20]).ratio() > 0.6


def respond(stt, brain, voice, audio, followup=False):
    """Returns True if something was answered."""
    face.state('thinking')
    t = time.time(); text = transcribe(stt, audio); t_stt = time.time() - t
    log(f'heard ({t_stt:.1f}s){" [follow-up]" if followup else ""}: {text!r}')
    last = brain.history[-1]['content'] if brain.history else ''
    if followup and echo_of(text, last):
        log('ignored: echo of my own reply'); return False
    if followup and PHANTOMS.match(text.strip()):
        log(f'ignored: likely Whisper phantom {text!r}'); return False
    if len(text) >= 2:
        tell_robot('speech', text)                         # not Buddy's own echo
    if len(text) < 2 or (followup and len(text.split()) < 2):  # stray noise in the follow-up window: stay quiet
        if not followup:
            voice.say("Sorry, I didn't catch that.")
        return False
    face.state('thinking', text)
    t = time.time(); reply = brain.ask(text); t_llm = time.time() - t
    log(f'reply ({t_llm:.1f}s): {reply!r}')
    voice.say(reply)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--say'); ap.add_argument('--ask'); ap.add_argument('--wav')
    args = ap.parse_args()
    voice = Voice()
    if args.say:
        voice.say(args.say); return
    brain = Brain()
    if args.ask:
        r = brain.ask(args.ask); log('reply:', repr(r)); voice.say(r); return
    from openwakeword.model import Model
    oww = Model(wakeword_models=[WAKE_MODEL], inference_framework='onnx')
    key = list(oww.models)[0]
    stt = load_stt()
    face.start(); face.state('idle')
    keep_usb_input()
    try:
        import media_screen as ms
        ms.show_face()                                          # idle screen on the Den TV unless media is up
    except Exception as e:
        log(f'face screen skipped: {e}')
    log(f'ready: wake {key} @ {WAKE_TH}, STT on GPU, LLM {LLM_MODEL}, voice {VOICE}, mic {MIC}')

    if args.wav:                                             # offline test path
        import soundfile as sf
        a, sr = sf.read(args.wav, dtype='int16'); assert sr == SR, 'need 16 kHz'
        frames = iter([a[i:i + FRAME] for i in range(0, len(a) - FRAME, FRAME)])
        nxt = lambda: next(frames)
        try:
            while True:
                f = nxt()
                if oww.predict(f)[key] >= WAKE_TH:
                    log('WAKE (wav)'); cmd = record_command(nxt, -70)
                    if cmd is not None:
                        respond(stt, brain, voice, cmd)
                    oww.reset()
        except StopIteration:
            return

    mic = Mic()
    floors = []
    while True:
        f = mic.frame()
        floors.append(db(f)); floors = floors[-250:]           # ~20 s of idle level
        if time.time() - getattr(main, 'mus_t', 0.0) > 2.0:    # mouth = spectrum whenever the music player runs
            main.mus_t = time.time()
            try:
                import music
                if music.active() != face.music_on():
                    music._face(music.active())
                if music.active() and music._ipc('get_property', 'volume').get('data') != music.level():
                    music._ipc('set_property', 'volume', music.level())   # idle: never stay ducked (2026-10-02)
            except Exception:
                pass
        if time.time() - getattr(main, 'usb_t', 0.0) > 30.0:   # keep the Evo on USB Audio even while idle
            main.usb_t = time.time()
            threading.Thread(target=keep_usb_input, daemon=True).start()
        try:                                                   # robot arrived after "come check this out": say it
            import robot_eyes
            later = robot_eyes.pending.get_nowait()
        except Exception:
            later = None
        if not later and time.time() - getattr(main, 'say_t', 0.0) > 15.0:   # the robot's own voice (its self-care):
            main.say_t = time.time()                            # "please plug me in" etc. The robot queues, Buddy speaks.
            threading.Thread(target=robot_say_poll, daemon=True).start()
        if not later and robot_says:
            m = robot_says.pop(0)
            later = m['text']
            robot_say_ack(m['id'])
        if later:
            log(f'robot: {later}')
            voice.say(later); brain.history.append({'role': 'assistant', 'content': later})
            mic.ignore_until(time.time() + ECHO_TAIL_S)
            continue
        score = oww.predict(f)[key]
        tapped = getattr(face, 'listen_req', None) is not None and face.listen_req.is_set()
        if score < WAKE_TH and not tapped:
            continue
        if tapped:
            face.listen_req.clear()
        floor = float(np.percentile(floors, 20))
        log(f'{"TAP (touchscreen)" if tapped else "WAKE"} score {score:.2f} (room floor {floor:.1f} dBFS)')
        tell_robot('wake')
        ducked = duck_music()
        voice.chime()
        mic.ignore_until(time.time() + ECHO_TAIL_S)            # skip the chime: via the robot it lands ~0.5-0.75 s after /play returns (2026-10-06)
        cmd, followup = record_command(mic.frame, floor), False
        while cmd is not None:
            try:
                answered = respond(stt, brain, voice, cmd, followup)
            except Exception as e:
                log('ERROR', type(e).__name__, e)
                voice.say("Sorry, my brain isn't answering right now."); answered = False
            mic.ignore_until(time.time() + ECHO_TAIL_S)        # drop everything heard while Buddy spoke
            if not (answered and FOLLOWUP_S > 0):
                break
            # music stays low (ducked) through the follow-up and back up after; only a reply that just STARTED music
            # ends it (2026-10-01: music.py counts as 'on' even while ducked -> no follow-ups)
            if brain.started_play:
                log('music just started: no follow-up window'); break
            voice.cue(); mic.ignore_until(time.time() + ECHO_TAIL_S)
            log(f'follow-up window {FOLLOWUP_S:.0f}s')
            cmd, followup = record_command(mic.frame, floor, start_timeout=FOLLOWUP_S), True
        if cmd is None and not followup:
            log('no command heard')
        if ducked and not brain.used_media:                    # resume unless the user changed the music
            unduck_music()
        oww.reset()
        face.state('idle')
        log('waiting for wake word')


if __name__ == '__main__':
    main()
