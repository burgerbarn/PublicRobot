"""The robot's body model, learned from its own practice (behavior/practice.py) - nothing here is set by hand.
Reads every ~/practice/*/trials.jsonl and works out, for each kind of move (ahead, diagonal, sideways, return leg,
turn), each speed, and each kind of floor it has seen (its own grouping of what the floor looked like before the
move): how much of the commanded distance it actually got, and how often it stalled.
Writes ~/practice/body_model.json and prints the table. Usage: python3 learn_body.py"""
import glob, json, math, os, time

import numpy as np

HOME = os.path.expanduser('~/practice')


def kind_of(t):
    if t['kind'] == 'turn': return 'turn'
    if t['kind'] == 'back': return 'return leg'
    d = abs(t['dir_deg'])
    return 'ahead' if d < 20 else 'diagonal' if d < 70 else 'sideways'


def measured(t):
    """Best measurement of the distance covered along the commanded direction: camera odometry, else the map pose."""
    for k in ('camera', 'map'):
        if t.get(k):
            return t[k][1], k
    return None, None


def floors(trials, k=2, iters=20):
    """Group the floor looks by colour (Lab a,b mean) into k kinds - the robot's own categories, no names given."""
    X = np.array([t['floor'][1:3] for t in trials if t.get('floor')], float)
    if len(X) < 2 * k:
        return None, None
    c = X[np.linspace(0, len(X) - 1, k).astype(int)].copy()
    for _ in range(iters):
        lab = np.argmin(((X[:, None, :] - c[None]) ** 2).sum(-1), 1)
        for j in range(k):
            if (lab == j).any(): c[j] = X[lab == j].mean(0)
    if np.linalg.norm(c[0] - c[1]) < 6.0:                    # the looks do not really differ: one kind of floor so far
        return np.array([X.mean(0)]), np.zeros(len(X), int)
    return c, lab


def main():
    trials = []
    for f in sorted(glob.glob(os.path.join(HOME, '*', 'trials.jsonl'))):
        trials += [json.loads(l) for l in open(f) if l.strip().startswith('{"k"')]
    if not trials:
        print('no practice yet'); return
    centers, lab = floors(trials)
    it = iter(lab) if lab is not None else None
    rows = {}
    for t in trials:
        fl = int(next(it)) if it is not None and t.get('floor') else 0
        if t['kind'] == 'turn':
            asked = t['wz'] * t['secs']; got = t.get('gyro_turn', 0.0)
            ratio = got / asked if abs(asked) > 1e-3 else None; speed = abs(t['wz'])
        else:
            got, _ = measured(t); ratio = None if got is None or t['asked_m'] < 1e-3 else got / t['asked_m']; speed = t['speed']
        if ratio is None:
            continue
        stall = bool(t.get('stopped_by')) or ratio < 0.15
        rows.setdefault((fl, kind_of(t), speed), []).append((ratio, stall))
    model = dict(updated=time.strftime('%Y-%m-%d %H:%M:%S'), trials=len(trials),
                 floors=None if centers is None else [dict(id=i, lab_ab=[round(float(v), 1) for v in c]) for i, c in enumerate(centers)],
                 moves=[])
    print(f'{len(trials)} practice moves; floor kinds seen: {0 if centers is None else len(centers)}')
    print('floor  move         speed   n   got/asked (median)  stalled')
    for (fl, kind, speed), v in sorted(rows.items()):
        r = float(np.median([x[0] for x in v])); s = float(np.mean([x[1] for x in v]))
        model['moves'].append(dict(floor=fl, move=kind, speed=speed, n=len(v), got_ratio=round(r, 2), stall_rate=round(s, 2)))
        print(f'  {fl}    {kind:11s}  {speed:4.2f}  {len(v):3d}        {r:5.2f}           {100 * s:3.0f} %')
    json.dump(model, open(os.path.join(HOME, 'body_model.json'), 'w'), indent=1)


if __name__ == '__main__':
    main()
