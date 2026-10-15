"""Driving limits come from what the robot learned about its own body (~/practice/body_model.json, written by
behavior/learn_body.py from its own practice moves) - not from numbers typed into nav2.yaml.
apply(cfg) patches a loaded nav2.yaml dict in place and returns what it changed (also written to
~/practice/applied_limits.json). With too little practice it changes nothing: nav2.yaml's values are only the starting
point the robot is born with."""
import json, os, time

MODEL = os.path.expanduser('~/practice/body_model.json')
MIN_TRIALS = 60           # practice moves before the model is used at all
MIN_N = 3                 # moves of one kind/speed/floor before that row counts
OK_STALL, OK_RATIO = 0.10, 0.60
CEIL = 0.35               # never above the driver's own limit


def works(rows, move):
    """Speeds of this kind of move that work on EVERY floor it has tried them on (enough tries, rarely stalls,
    gets most of the distance). Sorted."""
    by_speed = {}
    for r in rows:
        if r['move'] == move and r['n'] >= MIN_N:
            by_speed.setdefault(r['speed'], []).append(r['stall_rate'] <= OK_STALL and r['got_ratio'] >= OK_RATIO)
    return sorted(s for s, ok in by_speed.items() if all(ok))


def apply(cfg, model_path=MODEL):
    try:
        m = json.load(open(model_path))
    except (OSError, ValueError):
        return {'used': False, 'why': 'no body model yet'}
    if m.get('trials', 0) < MIN_TRIALS:
        return {'used': False, 'why': f"only {m.get('trials', 0)} practice moves (needs {MIN_TRIALS})"}
    rows = m.get('moves', [])
    f = cfg['controller_server']['ros__parameters']['FollowPath']
    vs = cfg['velocity_smoother']['ros__parameters']
    out = {'used': True, 'trials': m['trials'], 'changes': {}}
    ahead, side = works(rows, 'ahead'), works(rows, 'sideways')
    tried_side = any(r['move'] == 'sideways' and r['n'] >= MIN_N for r in rows)
    if ahead:
        vmax, vmin = min(CEIL, ahead[-1]), ahead[0]
        out['changes'].update(max_vel_x=vmax, max_speed_xy=vmax, min_speed_xy=vmin)
        f['max_vel_x'], f['max_speed_xy'], f['min_speed_xy'] = vmax, vmax, vmin
        vs['max_velocity'][0] = vmax
    if tried_side:                                           # it knows how sideways goes on its floors
        vy = min(CEIL, side[-1]) if side else 0.0
        out['changes'].update(max_vel_y=vy)
        f['max_vel_y'], f['min_vel_y'] = vy, -vy
        f['vy_samples'] = 5 if vy > 0 else 1
        vs['max_velocity'][1], vs['min_velocity'][1] = vy, -vy
    out['t'] = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        json.dump(out, open(os.path.join(os.path.dirname(model_path), 'applied_limits.json'), 'w'), indent=1)
    except OSError:
        pass
    return out
