"""Drive Buddy's screen (Samsung TV, HDMI) on bigbuddy: his face, the BUDDY JUKEBOX and BUDDY VIDEO web apps, in a
kiosk CHROMIUM driven over the Chrome DevTools Protocol (--remote-debugging-port). 2026-09-30: moved from a Firefox
kiosk (WebDriver BiDi) - Firefox rendered the 3D face at 3-12 fps on the 4K TV, Chromium at 127 fps. Same functions,
same behaviour. The apps' own page functions are called (userGesture so autoplay/fullscreen work).
Tabs: one tab on screen; faceplay = the face tab in front + the jukebox in a second (background) tab.
Page functions used (read from the apps 2026-09-28):
  jukebox: doSearch(q) -> currentTracks, playTrackAt(i), shuffleAndPlay(), nextTrack(), prevTrack(),
           <audio id="audio">, setLcdFullscreen(on)
  video:   playMovie(m) (m from /api/search or /api/random), stopPlayback(), enterVideoFullscreen(),
           #filterSelect (crt, vhs, film8mm, seccam, gameboy, ascii, off)
CLI test:  media_screen.py music "the beatles" | music | now | movie "monsters vs aliens" | movie
           | control pause|resume|next|previous|stop | style vhs | close"""
import asyncio, json, os, shutil, subprocess, sys, time

import requests
import websockets

BASE = os.environ.get('BUDDY_MEDIA_HOST', 'http://192.168.1.122')
VIDEO = os.environ.get('BUDDY_VIDEO', 'http://127.0.0.1:8282/')       # bigbuddy copy: no frame default, strengths, /api/remote
JUKEBOX = os.environ.get('BUDDY_JUKEBOX', 'http://127.0.0.1:8281/')   # bigbuddy copy: TV layout, artist search, /api/remote
PORT = int(os.environ.get('BUDDY_SCREEN_CDP_PORT', '9222'))
PROFILE = os.path.expanduser('~/.buddy-screen-chromium')
UNIT = 'buddy-screen'
SPK = os.environ.get('BUDDY_SPK', 'buddy_aec_sink')     # AEC reference sink -> HDMI (so Buddy can hear over the music)
STYLES = {'crt': 'crt', 'scanline': 'crt', 'vhs': 'vhs', '8mm': 'film8mm', 'film': 'film8mm', 'film8mm': 'film8mm',
          'security': 'seccam', 'seccam': 'seccam', 'security cam': 'seccam', 'gameboy': 'gameboy',
          'game boy': 'gameboy', 'ascii': 'ascii', 'off': 'off', 'none': 'off', 'normal': 'off'}
def _running():
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', f'{UNIT}.service']).returncode == 0


def ensure_browser():
    """Start the kiosk Chromium (own profile, transient user unit, window class buddy-screen) if not running."""
    if not _running():
        os.makedirs(PROFILE, exist_ok=True)
        subprocess.run(['systemctl', '--user', 'reset-failed', f'{UNIT}.service'], stderr=subprocess.DEVNULL)
        subprocess.run(['systemd-run', '--user', '-q', f'--unit={UNIT}', '--collect',
                        f'--setenv=PULSE_SINK={SPK}',          # sound to HDMI, not the default (Komplete Audio 6)
                        '--setenv=WAYLAND_DISPLAY=wayland-0',
                        shutil.which('chromium-browser') or shutil.which('chromium'), '--kiosk',
                        '--ozone-platform=wayland', '--class=buddy-screen',   # KWin finds it by this class (hotkey, modes)
                        f'--user-data-dir={PROFILE}', f'--remote-debugging-port={PORT}',
                        '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--noerrdialogs',
                        '--disable-session-crashed-bubble', '--disable-features=Translate,MediaRouter',
                        # faceplay plays in a background tab behind the face: never throttle/suspend hidden tabs/windows
                        # (2026-10-01: the jukebox never requested the song while hidden)
                        '--disable-background-media-suspend', '--disable-renderer-backgrounding',
                        '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
                        '--password-store=basic', '--hide-crash-restore-bubble', 'about:blank'], check=True)
    for _ in range(60):
        try:
            requests.get(f'http://127.0.0.1:{PORT}/json/version', timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError('kiosk Chromium did not open its DevTools port')


FACE = os.environ.get('BUDDY_FACE', 'http://127.0.0.1:8290/face')          # the 3D face (only face)


def _face_music(on):
    """Tell the face (same process as buddy_voice) to sing along / stop; no-op when run from the CLI."""
    try:
        import face_server
        if on or face_server.music_on():
            face_server.music(on, _caption if on else None)
    except Exception:
        pass


def _caption():
    try:
        st = requests.get(f'{JUKEBOX}api/remote/state', timeout=2).json()
        return f"{st['title']} — {st.get('artist') or ''}".strip(' —') if st.get('title') else ''
    except Exception:
        return ''


# ---- Chromium tabs (DevTools HTTP endpoints) + page commands (DevTools websocket)
def _url(path):
    return f'http://127.0.0.1:{PORT}{path}'


def _pages():
    ensure_browser()
    return [p for p in requests.get(_url('/json'), timeout=5).json() if p.get('type') == 'page']


def _front_and_bg():
    """(tab on screen, background tabs). With a face tab open, the face is in front (faceplay: jukebox behind it)."""
    ps = _pages()
    front = next((p for p in ps if p['url'].startswith(FACE)), None) or (ps[0] if ps else None)
    return front, [p for p in ps if p is not front]


def _new_tab(url):
    p = requests.put(_url(f'/json/new?{url}'), timeout=10).json()
    _wait_loaded(p, url)
    return p


def _activate(p):
    requests.get(_url(f"/json/activate/{p['id']}"), timeout=5)


def _close(p):
    requests.get(_url(f"/json/close/{p['id']}"), timeout=5)


class Cdp:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def cmd(self, method, params=None):
        self.n += 1
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get('id') == self.n:                          # (events have no id)
                if 'error' in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get('result', {})


def _run(page, fn):
    async def go():
        async with websockets.connect(page['webSocketDebuggerUrl'], max_size=None, origin=None) as ws:
            return await fn(Cdp(ws))
    return asyncio.run(go())


async def _eval(c, body):
    """Run an async JS body in the page WITH a user gesture (autoplay / WebAudio / fullscreen); returns its value."""
    r = await c.cmd('Runtime.evaluate', {'expression': f'(async () => {{ {body} }})()', 'awaitPromise': True,
                                         'returnByValue': True, 'userGesture': True})
    if 'exceptionDetails' in r:
        raise RuntimeError(r['exceptionDetails'].get('text', 'page error'))
    return r.get('result', {}).get('value')


def _wait_loaded(page, url, timeout=20):
    async def go(c):
        end = time.time() + timeout
        while time.time() < end:
            st = await _eval(c, 'return [document.readyState, location.href];')
            if st and st[0] == 'complete' and st[1].startswith(url):
                return True
            await asyncio.sleep(0.2)
        return False
    return _run(page, go)


def _navigate(page, url, settle=0.0):
    _run(page, lambda c: c.cmd('Page.navigate', {'url': url}))
    _wait_loaded(page, url)
    time.sleep(settle)                                         # page init() fetches /api/init


def _click(page):
    _run(page, lambda c: _eval(c, 'document.body.click(); return 1;'))   # user activation for autoplay/WebAudio


def _to_face():
    """After stop: back to the face (idle screen) instead of closing the kiosk."""
    front, bg = _front_and_bg()
    for p in bg:
        _close(p)
    _new_tab(FACE) if front is None else _navigate(front, FACE)
    _face_music(False)
    _place(FACE_OUTPUT)


def show_face():
    """Idle screen: Buddy's face full screen on the kiosk, unless music or a movie is on it."""
    front, bg = _front_and_bg()
    cur = front['url'] if front else ''
    if front is None:
        _new_tab(FACE); cur = FACE
    elif not (cur.startswith(JUKEBOX) or cur.startswith(VIDEO) or cur.startswith(FACE)):
        _navigate(front, FACE); cur = FACE
    if any(p['url'].startswith(JUKEBOX) for p in bg):
        _face_music(True)                                      # music still playing behind the face (restart)
    if cur.startswith(FACE):
        _place(FACE_OUTPUT)
    return cur


_FIND = "const w = workspace.windowList().find(w => w.resourceClass === 'buddy-screen');\n"
_MODE_JS = {
    'buddy': "if (w) { w.minimized = false; workspace.activeWindow = w; }",
    'desktop': "if (w) w.minimized = true;",
    # 25 % widget (owner 2026-09-30): out of full screen, bottom-right quarter of the work area, kept above other
    # windows; resizable (KDE: Alt+right-drag, or the window edges)
    'widget': ("if (w) { const a = workspace.clientArea(KWin.MaximizeArea, w); w.minimized = false; w.fullScreen = false;"
               " w.keepAbove = true; w.frameGeometry = {x: a.x + a.width / 2, y: a.y + a.height / 2,"
               " width: a.width / 2, height: a.height / 2}; workspace.activeWindow = w; }"),
    'full': "if (w) { w.minimized = false; w.keepAbove = false; w.fullScreen = true; workspace.activeWindow = w; }",
}


FACE_OUTPUT = os.environ.get('BUDDY_FACE_OUTPUT', 'DP-1')        # touchscreen (owner 2026-10-01): the face lives here
MEDIA_OUTPUT = os.environ.get('BUDDY_MEDIA_OUTPUT', 'DP-1')      # owner 2026-10-01: music + movies on the touchscreen too (TV = HDMI-A-1)


PLACE_ENABLED = os.environ.get('BUDDY_PLACE', '0') == '1'   # owner 2026-10-01: stop moving windows between screens


def _place(output):
    """Put the kiosk window full screen on a display (KWin output name). No-op if it is already there.
    OFF unless BUDDY_PLACE=1 (moving the kiosk between the HDR TV and the touchscreen broke the touchscreen)."""
    if not PLACE_ENABLED:
        return
    # size the window to the target screen before full screen. Moving a RUNNING Chromium from the TV (HDR, 2x) to the
    # touchscreen (SDR, 1.35x) drew, then went black (2026-10-01): the kiosk now starts on the touchscreen (DP-1 is
    # the primary display), so this only acts if it ever ends up elsewhere.
    _kwin("const o = workspace.screens.find(s => s.name === '%s');\n"
          "if (w && o && w.output.name !== o.name) { const g = o.geometry; w.fullScreen = false; workspace.sendClientToScreen(w, o); "
          "w.frameGeometry = {x: g.x, y: g.y, width: g.width, height: g.height}; w.fullScreen = true; }" % output,
          'buddyplace')


def _kwin(js, name):
    path = os.path.expanduser(f'~/.cache/{name}.js')
    with open(path, 'w') as f:
        f.write(_FIND + js)
    call = ['gdbus', 'call', '--session', '--dest', 'org.kde.KWin']
    subprocess.run(call + ['--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.unloadScript', name],
                   capture_output=True)
    out = subprocess.run(call + ['--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.loadScript', path, name],
                         capture_output=True, text=True).stdout
    sid = ''.join(c for c in out if c.isdigit())
    if sid:
        subprocess.run(call + ['--object-path', f'/Scripting/Script{sid}', '--method', 'org.kde.kwin.Script.run'], capture_output=True)
        time.sleep(0.3)


def set_mode(mode):
    """'buddy' = screen in front, 'desktop' = minimized, 'widget' = a 25 % window bottom-right (kept above),
    'full' = back to full screen. One-shot KWin script over D-Bus; the hotkey (Meta+Shift+B) is the installed KWin
    script kwin/buddytoggle."""
    if mode not in _MODE_JS:
        raise ValueError(mode)
    if mode != 'desktop' and not _running():
        show_face()
    path = os.path.expanduser('~/.cache/buddy-mode.js')
    with open(path, 'w') as f:
        f.write(_FIND + _MODE_JS[mode])
    call = ['gdbus', 'call', '--session', '--dest', 'org.kde.KWin']
    out = subprocess.run(call + ['--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.loadScript', path, 'buddymode'],
                         capture_output=True, text=True).stdout
    sid = ''.join(c for c in out if c.isdigit())
    if sid:
        subprocess.run(call + ['--object-path', f'/Scripting/Script{sid}', '--method', 'org.kde.kwin.Script.run'], capture_output=True)
        time.sleep(0.3)
    subprocess.run(call + ['--object-path', '/Scripting', '--method', 'org.kde.kwin.Scripting.unloadScript', 'buddymode'], capture_output=True)
    return {'buddy': 'Buddy mode.', 'desktop': 'Desktop mode.', 'widget': 'Widget mode.', 'full': 'Full screen.'}[mode]


def close_browser():
    subprocess.run(['systemctl', '--user', 'stop', f'{UNIT}.service'], stderr=subprocess.DEVNULL)


def _jukebox_behind_face():
    """Jukebox in a background tab (it plays and polls /api/remote there); the face stays on screen."""
    front, bg = _front_and_bg()
    if front is None:
        front = _new_tab(FACE)
    elif not front['url'].startswith(FACE):
        _navigate(front, FACE)
    jb = next((p for p in bg if p['url'].startswith(JUKEBOX)), None)
    if not jb:
        jb = _new_tab(JUKEBOX); time.sleep(1.5)
    _click(jb)
    _activate(front)                                           # the face back on screen
    _face_music(True)


def _on_screen(url):
    """Kiosk shows `url` alone (closing the other tab / navigating away stops its playback)."""
    front, bg = _front_and_bg()
    for p in bg:
        _close(p)                                              # music behind the face stops
    cur = front['url'] if front else ''
    if front is None:
        front = _new_tab(url); time.sleep(1.5)
    elif not cur.startswith(url):
        _navigate(front, url, 1.5)
    _click(front)                                              # user activation for autoplay/fullscreen
    return cur


def _jukebox_on_screen():
    """Make sure the kiosk shows the jukebox copy (its page polls /api/remote)."""
    _on_screen(JUKEBOX)
    _place(MEDIA_OUTPUT)


def _remote(cmd, arg=None, app=None):
    requests.post(f'{app or JUKEBOX}api/remote', json={'cmd': cmd, 'arg': arg}, timeout=5).raise_for_status()


def _screen_app():
    """'music' | 'video' | None - which app the kiosk shows."""
    if not _running():
        return None
    front, bg = _front_and_bg()
    cur = front['url'] if front else ''
    if cur.startswith(JUKEBOX) or any(p['url'].startswith(JUKEBOX) for p in bg):
        return 'music'
    return 'video' if cur.startswith(VIDEO) else None


def play_album():
    """The current song's album from track 1; when it ends the jukebox goes back to shuffling everything."""
    st = requests.get(f'{JUKEBOX}api/remote/state', timeout=5).json()
    if not st.get('album'):
        return 'Nothing is playing, so there is no current album.'
    _remote('album')
    return f'Playing the album "{st["album"]}" by {st.get("artist") or "unknown"} from track 1, then back to shuffle.'


def now_playing():
    try:
        st = requests.get(f'{JUKEBOX}api/remote/state', timeout=5).json()
    except Exception:
        return 'Nothing is playing.'
    if not st.get('title') or time.time() - st.get('t', 0) > 30:
        return 'Nothing is playing.'
    return f"{'Playing' if st.get('playing') else 'Paused on'} {st['title']} by {st.get('artist') or 'unknown artist'}" + \
           (f" from {st['album']}." if st.get('album') else '.')


def play_music(query=None, face=False):
    """query = artist, song title, or album words (artist matches come first). None = shuffle everything.
    face=True (faceplay): the jukebox plays in a background tab; Buddy's face stays up and its mouth plays the music."""
    if query:
        n = len(requests.get(f'{JUKEBOX}api/search', params={'q': query}, timeout=20).json().get('tracks', []))
        if not n:
            return f'Nothing in the jukebox matches "{query}".'
    set_mode('buddy')                                          # from desktop mode: the TV shows it
    _jukebox_behind_face() if face else _jukebox_on_screen()
    _remote('play', query) if query else _remote('shuffle')
    time.sleep(2.5)
    return now_playing() if query else 'Shuffling the whole music library.'


STOP_WORDS = {'the', 'a', 'an', 'and', 'of', 'vs', 'versus', 'movie', 'film', 'play', 'part'}


def find_movie(title):
    """Plex search is literal ("vs" misses "vs."): try the phrase, then each significant word; best title match."""
    import difflib, re
    def search(q):
        return requests.get(f'{VIDEO}api/search', params={'q': q}, timeout=10).json().get('movies', [])
    found = search(title)
    if not found:
        words = [w for w in re.findall(r"[a-z0-9']+", title.lower()) if w not in STOP_WORDS and len(w) > 2]
        seen = set()
        for w in sorted(words, key=len, reverse=True)[:3]:
            for m in search(w):
                if m['key'] not in seen:
                    seen.add(m['key']); found.append(m)
    if not found:
        return None
    norm = lambda t: re.sub(r'[^a-z0-9 ]', '', (t or '').lower())
    return max(found, key=lambda m: difflib.SequenceMatcher(None, norm(title), norm(m.get('title'))).ratio())


def play_movie(title=None, channel=None):
    """Play a movie by title (forgiving search), or a random one (optionally from a channel)."""
    if title:
        m = find_movie(title)
        if not m:
            return f'No movie matching "{title}".'
        set_mode('buddy'); _on_screen(VIDEO); _place(MEDIA_OUTPUT); _face_music(False); _remote('play', {'movie': m}, VIDEO)
        return f"Playing {m.get('title', 'a movie')}" + (f" ({m['year']})" if m.get('year') else '') + '.'
    arg = {}
    if channel:
        chans = requests.get(f'{VIDEO}api/init', timeout=10).json().get('channel_list', [])
        c = next((c for c in chans if channel.lower() in c['title'].lower()), None)
        if c:
            arg['channel_index'] = c['index']                  # the app's /api/random wants the channel INDEX
    set_mode('buddy'); _on_screen(VIDEO); _place(MEDIA_OUTPUT); _face_music(False); _remote('random', arg, VIDEO)
    time.sleep(4)
    st = requests.get(f'{VIDEO}api/remote/state', timeout=5).json()
    return f"Playing {st.get('title') or 'a random movie'}."


def control(action):
    """pause | resume | next | previous | stop — music (jukebox copy) or movie (video copy)."""
    app = _screen_app()
    if not app:
        return 'Nothing is playing.'
    cmd = {'previous': 'prev'}.get(action, action)
    if app == 'music':
        if cmd not in ('pause', 'resume', 'next', 'prev', 'stop'):
            return f'unknown action {action}'
        _remote(cmd)
    else:
        if cmd in ('next', 'prev'):
            return 'Skipping only works for music. Ask for another movie instead.'
        if cmd not in ('pause', 'resume', 'stop'):
            return f'unknown action {action}'
        _remote(cmd, None, VIDEO)
    if action == 'stop':
        time.sleep(1); _to_face()
    return {'pause': 'Paused.', 'resume': 'Resuming.', 'next': 'Next track.', 'previous': 'Previous track.',
            'stop': 'Stopped.'}[action]


def set_style(name, strength=None):
    """Movie look: crt | vhs | 8mm | security cam | gameboy | ascii | off; strength 0-150 % (default per style)."""
    v = STYLES.get((name or '').lower().strip())
    if not v:
        return 'Unknown style. Options: CRT, VHS, 8mm film, security cam, Game Boy, ASCII, or off.'
    if _screen_app() != 'video':
        return 'Styles only apply while a movie is playing.'
    _remote('style', {'name': v, 'strength': strength}, VIDEO)
    return 'Style off.' if v == 'off' else f'{name} look' + (f' at {strength} percent.' if strength else '.')


def set_fullscreen(on):
    """Movie: whole-screen view vs the page (player top 2/3). Music: the jukebox's LCD fullscreen."""
    app = _screen_app()
    if not app:
        return 'Nothing is on the screen.'
    _remote('fullscreen', {'on': bool(on)}, VIDEO if app == 'video' else JUKEBOX)
    return 'Fullscreen.' if on else 'Back to the normal view.'


def set_frame(on):
    if _screen_app() != 'video':
        return 'The TV frame only applies while a movie is playing.'
    _remote('frame', {'on': bool(on)}, VIDEO)
    return 'TV frame on.' if on else 'TV frame off.'


if __name__ == '__main__':
    a = sys.argv[1:]
    cmd = a[0] if a else ''
    if cmd == 'music':
        print(play_music(a[1] if len(a) > 1 else None))
    elif cmd == 'faceplay':
        print(play_music(a[1] if len(a) > 1 else None, face=True))
    elif cmd == 'mode':
        print(set_mode(a[1]))
    elif cmd == 'now':
        print(now_playing())
    elif cmd == 'movie':
        print(play_movie(a[1] if len(a) > 1 else None))
    elif cmd == 'control':
        print(control(a[1]))
    elif cmd == 'style':
        print(set_style(a[1], int(a[2]) if len(a) > 2 else None))
    elif cmd == 'frame':
        print(set_frame(a[1] == 'on'))
    elif cmd == 'fullscreen':
        print(set_fullscreen(a[1] == 'on'))
    elif cmd == 'close':
        close_browser(); print('closed')
    else:
        print(__doc__)
