"""Home Assistant lights for Buddy (HA on server, REST API). Lights only: never switches (the Den area also holds
the Evo 150 amp switches). Token: ~/voice/ha_token (0600, placed by the owner). HA URL: 192.168.1.122 first;
server's IPv6 addresses as fallback (Docker Desktop host networking dropped IPv4 once, fixed by a restart,
2026-09-28). CLI: home.py list | on den [50] | off drums | status den | amp status|on|off|source tv|volume 30"""
import json, os, sys, time

import requests

URLS = os.environ.get('BUDDY_HA_URLS', 'http://192.168.1.122:8123,http://[fded:6632:dece:42eb:8bf:8a47:c9ea:f4c5]:8123,'
                      'http://[fded:6632:dece:42eb:1c88:d624:2b54:8065]:8123').split(',')
TOKEN_FILE = os.path.expanduser('~/voice/ha_token')
_cache = {'t': 0, 'areas': {}, 'lights': {}}


def _req(method, path, **kw):
    h = {'Authorization': f'Bearer {open(TOKEN_FILE).read().strip()}', 'Content-Type': 'application/json'}
    last = None
    for u in URLS:
        try:
            r = requests.request(method, u + path, headers=h, timeout=6, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
    raise RuntimeError(f'Home Assistant unreachable: {last}')


def _refresh():
    if time.time() - _cache['t'] < 300:
        return
    tpl = ('{% set ns = namespace(o={}) %}{% for a in areas() %}{% set ns.o = dict(ns.o, **{area_name(a): '
           'area_entities(a) | select("match", "light\\\\.") | list}) %}{% endfor %}{{ ns.o | tojson }}')
    _cache['areas'] = {k.lower(): v for k, v in json.loads(_req('POST', '/api/template', json={'template': tpl}).text).items()}
    states = _req('GET', '/api/states').json()
    _cache['lights'] = {s['entity_id']: s for s in states if s['entity_id'].startswith('light.')}
    _cache['t'] = time.time()


def _resolve(target):
    """Area name -> its lights; else a light by friendly name or entity id. Returns (label, [entity_ids])."""
    _refresh()
    t = (target or '').lower().strip()
    for suffix in (' lights', ' light', ' room'):
        if t.endswith(suffix) and t[:-len(suffix)] in _cache['areas']:
            t = t[:-len(suffix)]
    if t in _cache['areas'] and _cache['areas'][t]:
        return t, _cache['areas'][t]
    for e, s in _cache['lights'].items():
        name = (s['attributes'].get('friendly_name') or '').lower()
        if t in (name, e, e.split('.', 1)[1]) or (t and t in name):
            return s['attributes'].get('friendly_name', e), [e]
    return None, []


def names():
    """Short catalogue for the LLM tool description."""
    _refresh()
    areas = [a for a, ents in _cache['areas'].items() if ents]
    lights = [s['attributes'].get('friendly_name', e) for e, s in _cache['lights'].items() if s['state'] != 'unavailable']
    return f"areas: {', '.join(areas)}; lights: {', '.join(lights)}"


def set_lights(target, action='on', brightness=None):
    label, ents = _resolve(target)
    if not ents:
        return f'No light or area called "{target}". Known {names()}.'
    live = [e for e in ents if _cache['lights'].get(e, {}).get('state') != 'unavailable']
    if not live:
        return f'The {label} lights are unavailable right now.'
    service = {'on': 'turn_on', 'off': 'turn_off', 'toggle': 'toggle'}.get(action)
    if not service:
        return f'unknown action {action}'
    data = {'entity_id': live}
    if brightness is not None and service == 'turn_on':
        data['brightness_pct'] = max(1, min(100, int(brightness)))
    _req('POST', f'/api/services/light/{service}', json=data)
    _cache['t'] = 0                                        # states changed
    what = f'{label} lights' if len(live) > 1 else label
    extra = f' at {data["brightness_pct"]} percent' if 'brightness_pct' in data else ''
    skipped = f' ({len(ents) - len(live)} unavailable)' if len(live) < len(ents) else ''
    return f'Turned {"on" if service == "turn_on" else "off" if service == "turn_off" else "toggled"} the {what}{extra}{skipped}.'


def status(target):
    label, ents = _resolve(target)
    if not ents:
        return f'No light or area called "{target}".'
    _cache['t'] = 0; _refresh()
    on = [_cache['lights'][e]['attributes'].get('friendly_name', e) for e in ents if _cache['lights'].get(e, {}).get('state') == 'on']
    if len(ents) == 1:
        return f'The {label} light is {"on" if on else "off"}.'
    if not on:
        return f'The {label} lights are off.'
    return f'{len(on)} of {len(ents)} {label} lights are on: ' + ', '.join(on) + '.'


# ---- Evo 150 amp (owner OK 2026-09-28). Allowlist: exactly this entity. TV not in HA yet. ----
AMP = os.environ.get('BUDDY_AMP_ENTITY', 'media_player.evo_150_2')
AMP_MAX_VOL = 80                                          # percent; the amp's own limit is 90


def _amp_state():
    return _req('GET', f'/api/states/{AMP}').json()


def amp(action, value=None):
    """on | off | source <name> | volume <0-80> | volume_up | volume_down | mute | unmute | status"""
    st = _amp_state()
    a = st['attributes']
    if action == 'status':
        if st['state'] in ('off', 'unavailable', 'standby'):
            return f"The Evo 150 is {st['state']}."
        vol = a.get('volume_level')
        return (f"The Evo 150 is {st['state']} on {a.get('source') or 'no input'}"
                + (f", volume {round(vol * 100)} percent" if vol is not None else '') + ('' if not a.get('is_volume_muted') else ', muted') + '.')
    if action in ('on', 'off'):
        _req('POST', f'/api/services/media_player/turn_{action}', json={'entity_id': AMP})
        return f'Evo 150 turned {action}.'
    if action == 'source':
        srcs = a.get('source_list') or []
        want = (value or '').lower()
        pick = next((x for x in srcs if x.lower() == want), None) or next((x for x in srcs if want and want in x.lower()), None)
        if not pick:
            return f"No input called {value}. Inputs: {', '.join(srcs)}."
        _req('POST', '/api/services/media_player/select_source', json={'entity_id': AMP, 'source': pick})
        return f'Evo 150 switched to {pick}.'
    if action in ('volume', 'volume_up', 'volume_down'):
        cur = round((a.get('volume_level') or 0) * 100)
        new = {'volume': int(value or cur), 'volume_up': cur + 5, 'volume_down': cur - 5}[action]
        new = max(0, min(AMP_MAX_VOL, new))
        _req('POST', '/api/services/media_player/volume_set', json={'entity_id': AMP, 'volume_level': new / 100})
        capped = ' (that is my limit)' if action != 'volume_down' and new == AMP_MAX_VOL else ''
        return f'Evo 150 volume {new} percent{capped}.'
    if action in ('mute', 'unmute'):
        _req('POST', '/api/services/media_player/volume_mute', json={'entity_id': AMP, 'is_volume_muted': action == 'mute'})
        return f'Evo 150 {action}d.'
    return f'unknown amp action {action}'


# ---- Den TV (Samsung QN43QN90F, static 192.168.1.110; owner OK 2026-09-28). Allowlist: this entity only. ----
TV = os.environ.get('BUDDY_TV_ENTITY', 'media_player.den_den_tv')


def tv(action, value=None):
    """on | off | source <TV|HDMI> | status"""
    st = _req('GET', f'/api/states/{TV}').json()
    if action == 'status':
        src = st['attributes'].get('source')
        return f"The Den TV is {st['state']}" + (f" on {src}." if src and st['state'] == 'on' else '.')
    if action in ('on', 'off'):
        _req('POST', f'/api/services/media_player/turn_{action}', json={'entity_id': TV})
        return f'Den TV turned {action}.'
    if action == 'source':
        srcs = st['attributes'].get('source_list') or []
        pick = next((x for x in srcs if x.lower() == (value or '').lower()), None)
        if not pick:
            return f"No TV input called {value}. Inputs: {', '.join(srcs)}."
        _req('POST', '/api/services/media_player/select_source', json={'entity_id': TV, 'source': pick})
        return f'Den TV switched to {pick}.'
    return f'unknown tv action {action}'


def ensure_av():
    """Before playing media on bigbuddy's screen: TV on, Evo on, Evo on its TV input. Returns what changed."""
    done = []
    if _req('GET', f'/api/states/{TV}').json()['state'] != 'on':
        tv('on'); done.append('TV on')
        # the TV wakes the Evo over HDMI-CEC (owner): give CEC up to 10 s before commanding the amp ourselves
        for _ in range(10):
            time.sleep(1)
            if _amp_state()['state'] not in ('off', 'standby'):
                done.append('amp woke via CEC'); break
    st = _amp_state()
    if st['state'] in ('off', 'standby'):
        amp('on'); done.append('amp on'); time.sleep(2); st = _amp_state()
    src = (st['attributes'].get('source') or '')
    if not src.lower().startswith('tv'):
        amp('source', 'TV'); done.append('amp to TV input')
    return done


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] == 'list':
        print(names())
    elif a[0] in ('on', 'off', 'toggle'):
        print(set_lights(a[1], a[0], a[2] if len(a) > 2 else None))
    elif a[0] == 'status':
        print(status(a[1]))
    elif a[0] == 'tv':
        print(tv(a[1], a[2] if len(a) > 2 else None))
    elif a[0] == 'av':
        print(ensure_av())
    elif a[0] == 'amp':
        print(amp(a[1], a[2] if len(a) > 2 else None))
