"""Room modes, switched by voice (owner 2026-10-01). Sound is ALWAYS the Evo 150 over USB (no HDMI audio).
game  = Samsung TV on and the primary display (games/Steam open there); Buddy's face stays on the touchscreen.
buddy = Buddy-only: TV off, the touchscreen is the primary display.
appletv = Samsung on its HDMI input (Apple TV), Evo on TV ARC (the TV's sound); Buddy stops forcing the Evo to USB
          until another mode is chosen (MODE_FILE).
TV power via Home Assistant (the Samsung isn't attached to bigbuddy); displays via kscreen-doctor (set only, no
queries - queries poll the touchscreen over DDC/CI)."""
import os, subprocess

MODE_FILE = os.path.expanduser('~/.cache/buddy-room-mode')


def current():
    try:
        return open(MODE_FILE).read().strip()
    except OSError:
        return 'buddy'


ENV = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}', WAYLAND_DISPLAY='wayland-0')


def _screens(*args):
    # The two displays never share a position (2026-10-05): with both at 0,0 a full-screen Buddy took the big
    # display's size and the 1280x720 touchscreen showed its top-left corner ("all I see is one eye"). The
    # touchscreen keeps 0,0; the HDMI display sits to its right.
    subprocess.run(['kscreen-doctor', *args, 'output.DP-1.position.0,0', 'output.HDMI-A-1.position.1280,0'],
                   env=ENV, capture_output=True, timeout=15)


def _face_on_touchscreen():
    """Buddy's window full screen on the touchscreen, whatever it was before."""
    import media_screen
    try:
        media_screen._kwin("const o = workspace.screens.find(s => s.name === '%s');\n"
                           "if (w && o) { const g = o.geometry; w.minimized = false; w.fullScreen = false; w.keepAbove = false; "
                           "w.noBorder = false; workspace.sendClientToScreen(w, o); "
                           "w.frameGeometry = {x: g.x, y: g.y, width: g.width, height: g.height}; w.fullScreen = true; }"
                           % media_screen.FACE_OUTPUT, 'buddyplace')
    except Exception:
        pass


def set_mode(mode):
    import evo, home
    if mode in ('game', 'buddy', 'appletv'):
        open(MODE_FILE, 'w').write(mode)
    if mode == 'appletv':
        tv = home.tv('on'); home.tv('source', 'HDMI')
        amp = evo.set_source('tv arc')
        return f'Apple TV mode: {tv} TV on its HDMI input. {amp} I will leave the speakers on the TV until you switch modes.'
    if mode == 'game':
        tv = home.tv('on')
        _screens('output.HDMI-A-1.enable', 'output.HDMI-A-1.priority.1', 'output.DP-1.priority.2')
        _face_on_touchscreen()
        evo.ensure_usb(max_age=0)
        return f'Game mode: {tv} The TV is the main screen; sound stays on the Evo over USB.'
    if mode == 'buddy':
        tv = home.tv('off')
        _screens('output.DP-1.priority.1', 'output.HDMI-A-1.priority.2')
        _face_on_touchscreen()
        evo.ensure_usb(max_age=0)
        return f'Buddy mode: {tv} The touchscreen is the main screen.'
    return f'unknown mode {mode}'
