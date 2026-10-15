"""Where does the owner reappear after the robot loses them? Learned by the robot from its own experience.

Data: the mind's events.jsonl ('lost' = head pose + exit side when the owner left view, 'found' = head pose where they
were seen again + seconds later). Each lost->found pair within FOUND_WINDOW_S is one example. Model: per exit side, a
weighted histogram over head pan (pulses) of where the owner turned up, with the mean wrist per bin; newer examples
weigh more (half-life HALF_LIFE_D days). No labels, no Claude: the robot trains this itself, nightly
(rosorin-reappear.timer) and the mind reloads it at start. Untrained / too little data -> the mind falls back to
"keep looking toward where you left".

Run: python reappear.py   (reads ~/vision/mind/*/events.jsonl, writes ~/vision/reappear.json)"""
import glob, json, math, os, time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, 'reappear.json')
FOUND_WINDOW_S = 120.0       # a 'found' later than this after 'lost' is not the same episode
BIN = 50                     # pan bin width (pulses, 12 deg)
HALF_LIFE_D = 14.0
MIN_EXAMPLES = 3             # below this per side the mind uses its fallback


def train(root=os.path.join(HERE, 'mind')):
    examples = []
    for f in sorted(glob.glob(os.path.join(root, '*', 'events.jsonl'))):
        lost = None
        for line in open(f):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get('event') == 'lost':
                lost = e
            elif e.get('event') == 'found' and lost and 0 < e['t'] - lost['t'] < FOUND_WINDOW_S:
                examples.append((lost['side'], lost['pan'], e['pan'], e['wrist'], e['t'] - lost['t'], e['t']))
                lost = None
    now = time.time()
    model = {'trained': now, 'examples': len(examples), 'sides': {}}
    for side in (-1, 1):
        bins = {}
        for s, _, pan, wrist, dt, t in examples:
            if s != side:
                continue
            w = 0.5 ** ((now - t) / 86400.0 / HALF_LIFE_D)
            b = bins.setdefault(str(int(pan // BIN)), {'w': 0.0, 'wrist': 0.0, 'n': 0, 'dt': 0.0})
            b['w'] += w; b['wrist'] += w * wrist; b['dt'] += w * dt; b['n'] += 1
        for b in bins.values():
            b['wrist'] /= b['w']; b['dt'] /= b['w']
        model['sides'][str(side)] = {'n': sum(b['n'] for b in bins.values()), 'bins': bins}
    json.dump(model, open(MODEL + '.tmp', 'w'), indent=1); os.replace(MODEL + '.tmp', MODEL)
    return model


def load():
    try:
        return json.load(open(MODEL))
    except (OSError, ValueError):
        return None


def predict(model, side):
    """(pan, wrist) where the owner most likely reappears after leaving toward `side`, or None if not learned yet."""
    s = (model or {}).get('sides', {}).get(str(side))
    if not s or s['n'] < MIN_EXAMPLES:
        return None
    k, b = max(s['bins'].items(), key=lambda kv: kv[1]['w'])
    return (int(k) + 0.5) * BIN, b['wrist']


if __name__ == '__main__':
    m = train()
    print(json.dumps({'examples': m['examples'], 'per_side': {k: v['n'] for k, v in m['sides'].items()},
                      'predict': {s: predict(m, s) for s in (-1, 1)}}))
