"""Replay of the mind's own thought log through the brain with a changed prompt/state - the off-floor test bench for
its decisions (like bag replay for navigation). Nothing is deployed and nothing moves: it only asks the brain again.
Usage: mind_replay.py [n frames]"""
import base64, collections, json, os, random, sys, time
import requests
sys.path.insert(0, os.path.expanduser('~/vision'))
import think
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
D = os.path.expanduser('~/vision/think/20261005'); random.seed(11)
rows = []
for l in open(D + '/thoughts.jsonl'):
    try: t = json.loads(l)
    except ValueError: continue
    if t.get('frame') and os.path.exists(D + '/' + t['frame']) and t.get('skill'): rows.append(t)
pick = random.sample(rows, N)
known = json.load(open(os.path.expanduser('~/vision/learned_objects.json')))
w = json.load(open(os.path.expanduser('~/vision/world.json')))
known = sorted(set(known) | set((w.get('objects') or {}).keys() if isinstance(w.get('objects'), dict) else []))
MEMORY = ('\nWhat you already know (do NOT identify these again; identify is only for something that is not on this list): '
          + ', '.join(known) + '. You know your owner Matt by sight.'
          '\nWhat your last choices led to: {recent}')
def ask(t, variant):
    st = dict(t['state'])
    prompt = think.PROMPT.format(state=json.dumps(st), skills='\n'.join(f'- {k}: {v}' for k, v in think.SKILLS.items()))
    if variant == 'no_identify':                       # noticing things is automatic; the menu holds only choices that DO something
        sk = {k: v for k, v in think.SKILLS.items() if k != 'identify'}
        prompt = think.PROMPT.format(state=json.dumps(st), skills='\n'.join(f'- {k}: {v}' for k, v in sk.items()))
        prompt = prompt.replace('\nLook at the camera image', '\nEverything your detector sees is written to your memory automatically; you do not need to do anything for that.\nLook at the camera image')
    if variant == 'memory':
        i = rows.index(t); rec = [f"{r['skill']}{' ' + str((r.get('args') or {}).get('object')) if r['skill'] == 'identify' else ''}" for r in rows[max(0, i - 4):i]]
        prompt = prompt.replace('\nLook at the camera image', MEMORY.format(recent='; '.join(rec) + ' (identify only writes a note; nothing else happens)') + '\nLook at the camera image')
    jpg = open(D + '/' + t['frame'], 'rb').read()
    body = {'model': think.MODEL, 'max_tokens': 160, 'temperature': 0.3, 'reasoning_effort': 'none', 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(jpg).decode()}}, {'type': 'text', 'text': prompt}]}]}
    try:
        txt = requests.post(think.URL, json=body, timeout=40).json()['choices'][0]['message']['content'] or ''
        d = json.loads(txt[txt.index('{'):txt.rindex('}') + 1]); return d.get('skill'), (d.get('args') or {}).get('object') or (d.get('args') or {}).get('question'), d.get('thought', '')[:80]
    except Exception as e:
        return 'ERROR', str(e)[:40], ''
out = {}
for v in (sys.argv[2:] or ['as_deployed', 'memory']):
    c = collections.Counter(); ex = []
    for t in pick:
        s, a, th = ask(t, v); c[s] += 1
        if len(ex) < 6: ex.append((t['skill'], s, a, th))
    out[v] = c
    print(v, dict(c.most_common()))
    for e in ex: print('    logged:', e[0], '| now:', e[1], e[2], '|', e[3])
print('logged at the time:', dict(collections.Counter(t['skill'] for t in pick).most_common()))
