"""Cambridge Audio Evo 150 - direct control over its own StreamMagic API (owner 2026-10-01: no Home Assistant).
Audio comes in on USB (bigbuddy -> USB_AUDIO); this is the control side: input, volume, power, mute, status.
API (local, no auth): GET http://<evo>/smoip/zone/state[?source=ID|volume_percent=N|mute=true|false],
/smoip/system/sources, /smoip/system/power?power=ON|NETWORK.
CLI: evo.py status | source usb | volume 40 | volume_up | volume_down | mute | unmute | on | off"""
import os, sys, time

import requests

EVO = os.environ.get('BUDDY_EVO_URL', 'http://192.168.1.11')
USB = 'USB_AUDIO'


def _get(path, **params):
    r = requests.get(EVO + path, params=params, timeout=3)
    r.raise_for_status()
    return r.json().get('data', {})


def state():
    return _get('/smoip/zone/state')


def sources():
    return [(s['id'], s['name']) for s in _get('/smoip/system/sources').get('sources', [])]


def status():
    st = state()
    if not st.get('power'):
        return 'The Evo 150 is off (standby).'
    name = dict(sources()).get(st.get('source'), st.get('source'))
    return f"The Evo 150 is on {name}, volume {st.get('volume_percent')} percent" + (', muted.' if st.get('mute') else '.')


def set_source(want):
    """want = an id (USB_AUDIO) or a name/part of one ("usb", "turntable", "tv arc")."""
    w = (want or '').lower().replace(' ', '')
    srcs = sources()
    pick = next((i for i, n in srcs if w in (i.lower().replace('_', ''), n.lower().replace(' ', ''))), None) or \
        next((i for i, n in srcs if w and (w in n.lower().replace(' ', '') or w in i.lower().replace('_', ''))), None)
    if not pick:
        return f"No input called {want}. Inputs: {', '.join(n for _, n in srcs)}."
    _get('/smoip/zone/state', source=pick)
    return f'Evo 150 switched to {dict(srcs)[pick]}.'


def set_volume(pct):
    pct = max(0, min(80, int(pct)))                    # 80 cap as before (owner's amp tool)
    _get('/smoip/zone/state', volume_percent=pct)
    return f'Evo 150 volume {pct} percent.'


def step_volume(delta):
    return set_volume(int(state().get('volume_percent') or 0) + delta)


def mute(on):
    _get('/smoip/zone/state', mute='true' if on else 'false')
    return 'Evo 150 muted.' if on else 'Evo 150 unmuted.'


def power(on):
    _get('/smoip/system/power', power='ON' if on else 'NETWORK')
    return f"Evo 150 turned {'on' if on else 'off'}."


_check = {'t': 0.0}


def ensure_usb(max_age=30.0):
    """Buddy's sound comes in on USB_AUDIO. If the Evo is on but on another input (it fell back to TV after a power
    cycle), switch it back. Cached max_age s; returns a message only when it changed something."""
    if time.time() - _check['t'] < max_age:
        return None
    try:                                                   # Apple TV mode: the Evo is on TV ARC on purpose
        if open(os.path.expanduser('~/.cache/buddy-room-mode')).read().strip() == 'appletv':
            return None
    except OSError:
        pass
    _check['t'] = time.time()
    st = state()
    if st.get('power') and st.get('source') != USB:
        _get('/smoip/zone/state', source=USB)
        return f"Evo input was {st.get('source')}, switched back to USB Audio."
    return None


def control(action, value=None):
    """The voice tool: status | on | off | source <name> | volume <0-80> | volume_up | volume_down | mute | unmute."""
    if action == 'status': return status()
    if action in ('on', 'off'): return power(action == 'on')
    if action == 'source': return set_source(value)
    if action == 'volume': return set_volume(value)
    if action == 'volume_up': return step_volume(+5)
    if action == 'volume_down': return step_volume(-5)
    if action in ('mute', 'unmute'): return mute(action == 'mute')
    return f'unknown action {action}'


if __name__ == '__main__':
    a = sys.argv[1:] or ['status']
    print(control(a[0], a[1] if len(a) > 1 else None))
